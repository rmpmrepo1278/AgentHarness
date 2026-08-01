"""Docker tool implementations for the Docker MCP server."""
from __future__ import annotations
import json
import os
import re
import time
import logging
import subprocess

import yaml

import docker

import templates
import port_allocator
import resource_guard
import secrets as secrets_mgr
import image_search

log = logging.getLogger("docker_tools")

_client = None


def _docker():
    global _client
    if _client is None:
        _client = docker.from_env()
    return _client


def list_containers(args: dict) -> dict:
    """List all containers, optionally filtered by name pattern."""
    name_filter = args.get("filter", "")
    containers = _docker().containers.list(all=True)
    result = []
    for c in containers:
        if name_filter and name_filter.lower() not in c.name.lower():
            continue
        try:
            image = c.image.tags[0] if c.image and c.image.tags else (c.image.short_id if c.image else "unknown")
        except Exception:
            image = "unknown"
        result.append({
            "name": c.name,
            "status": c.status,
            "image": image,
            "ports": {str(k): str(v) for k, v in (c.ports or {}).items() if v},
            "created": c.attrs.get("Created", ""),
        })
    return {"containers": result, "count": len(result)}


def container_status(args: dict) -> dict:
    """Get detailed status of a container."""
    name = args.get("name")
    if not name:
        raise ValueError("container name required")
    try:
        c = _docker().containers.get(name)
    except docker.errors.NotFound:
        return {"error": f"Container '{name}' not found"}

    state = c.attrs.get("State", {})
    net = c.attrs.get("NetworkSettings", {})
    mounts = c.attrs.get("Mounts", [])
    return {
        "name": c.name,
        "status": c.status,
        "health": state.get("Health", {}).get("Status", "none"),
        "started_at": state.get("StartedAt", ""),
        "restart_count": c.attrs.get("RestartCount", 0),
        "image": (c.image.tags[0] if c.image and c.image.tags else (c.image.short_id if c.image else "unknown")),
        "ports": {str(k): str(v) for k, v in (c.ports or {}).items() if v},
        "mounts": [{"source": m.get("Source", ""), "destination": m.get("Destination", "")}
                   for m in mounts],
        "networks": list(net.get("Networks", {}).keys()),
    }


def container_logs(args: dict) -> dict:
    """Get recent logs from a container."""
    name = args.get("name")
    tail = args.get("tail", 50)
    if not name:
        raise ValueError("container name required")
    try:
        c = _docker().containers.get(name)
    except docker.errors.NotFound:
        return {"error": f"Container '{name}' not found"}

    logs = c.logs(tail=tail, timestamps=True).decode("utf-8", errors="replace")
    return {"name": name, "logs": logs, "lines": len(logs.strip().split("\n"))}


def restart_container(args: dict) -> dict:
    """Restart a container."""
    name = args.get("name")
    if not name:
        raise ValueError("container name required")
    try:
        c = _docker().containers.get(name)
        c.restart(timeout=30)
        return {"name": name, "status": "restarted"}
    except docker.errors.NotFound:
        return {"error": f"Container '{name}' not found"}


def remove_container(args: dict) -> dict:
    """Stop and remove a container."""
    name = args.get("name")
    remove_volumes = args.get("remove_volumes", False)
    if not name:
        raise ValueError("container name required")

    protected = {"chaguli", "mcp-gateway", "docker-mcp", "llama-server"}
    if name in protected:
        return {"error": f"Cannot remove protected container '{name}'"}

    try:
        c = _docker().containers.get(name)
        c.stop(timeout=30)
        c.remove(v=remove_volumes)
        return {"name": name, "status": "removed", "volumes_removed": remove_volumes}
    except docker.errors.NotFound:
        return {"error": f"Container '{name}' not found"}


def _try_self_heal(stack_name: str, crash_looping: list, compose_yaml: str,
                    compose_path: str, variables: dict) -> dict | None:
    """Analyze crash-loop logs and attempt automatic fixes.
    Returns a deploy result dict if fixed, or None if can't fix."""

    all_logs = "\n".join(c.get("logs", "") for c in crash_looping).lower()

    fixes_applied = []
    new_yaml = compose_yaml

    # Parse the compose YAML
    try:
        compose = yaml.safe_load(compose_yaml)
    except Exception:
        return None

    services = compose.get("services", {})
    if not services:
        return None
    service_name = list(services.keys())[0]
    service = services[service_name]

    # ── Fix 1: Missing volume / persistent storage ─────────────────────────
    # Common patterns: "no persistent volume", "VOLATILE_STORAGE", "permission denied /data"
    volume_patterns = [
        r"volatile.storage", r"persistent.volume", r"no.data.directory",
        r"permission denied.*(/data|/config|/db|/storage)",
        r"mkdir.*permission denied", r"read-only file system",
    ]
    needs_volume = any(re.search(p, all_logs) for p in volume_patterns)

    if needs_volume:
        # Extract paths that need volumes from the error logs
        volume_paths = set()
        # Look for paths mentioned in errors
        path_matches = re.findall(r'(?:permission denied|cannot create|no such file).*?(/\w[\w/.-]+)', all_logs)
        for p in path_matches:
            # Only add data-like paths
            if any(d in p for d in ["/data", "/config", "/db", "/storage", "/media", "/uploads"]):
                volume_paths.add(p)

        # Common default paths for popular apps
        if not volume_paths:
            common_data_paths = ["/data", "/config", "/app/data"]
            for p in common_data_paths:
                volume_paths.add(p)

        # Add volume mounts
        existing_volumes = service.get("volumes", [])
        data_dir = "/opt/%s" % stack_name
        for vpath in volume_paths:
            mount = "%s%s:%s" % (data_dir, vpath, vpath)
            if mount not in existing_volumes:
                existing_volumes.append(mount)
                fixes_applied.append("Added volume: %s -> %s" % (data_dir + vpath, vpath))

        service["volumes"] = existing_volumes
        log.info("Self-heal: added %d volume(s) for %s" % (len(volume_paths), stack_name))

    # ── Fix 2: Missing environment variables ───────────────────────────────
    env_patterns = [
        (r"(database_url|db_url).*not set", {"DATABASE_URL": "sqlite:///data/db.sqlite3"}),
        (r"secret.key.*not set|no secret", {"SECRET_KEY": "__AUTO_GENERATE__"}),
        (r"timezone.*not set", {"TZ": "America/Los_Angeles"}),
    ]

    existing_env = service.get("environment", {})
    if isinstance(existing_env, list):
        # Convert list format to dict
        env_dict = {}
        for e in existing_env:
            if "=" in str(e):
                k, _, v = str(e).partition("=")
                env_dict[k] = v
        existing_env = env_dict

    for pattern, env_vars in env_patterns:
        if re.search(pattern, all_logs):
            for k, v in env_vars.items():
                if k not in existing_env:
                    if v == "__AUTO_GENERATE__":
                        import secrets as _s, string
                        v = "".join(_s.choice(string.ascii_letters + string.digits) for _ in range(32))
                    existing_env[k] = v
                    fixes_applied.append("Added env: %s" % k)

    if existing_env and fixes_applied:
        service["environment"] = existing_env

    # ── Fix 3: Port conflicts ──────────────────────────────────────────────
    if "address already in use" in all_logs or "port is already allocated" in all_logs:
        ports = service.get("ports", [])
        new_ports = []
        for p in ports:
            host_port = str(p).split(":")[0]
            try:
                new_port = port_allocator.find_free_port(int(host_port))
                new_ports.append("%d:%s" % (new_port, str(p).split(":")[-1]))
                if new_port != int(host_port):
                    fixes_applied.append("Port %s -> %d (was in use)" % (host_port, new_port))
            except Exception:
                new_ports.append(p)
        service["ports"] = new_ports

    # ── Apply fixes and redeploy ───────────────────────────────────────────
    if not fixes_applied:
        return None

    log.info("Self-heal: applying %d fix(es) for %s: %s" % (
        len(fixes_applied), stack_name, "; ".join(fixes_applied)))

    # Rebuild compose YAML
    compose["services"][service_name] = service
    new_yaml = yaml.dump(compose, default_flow_style=False)

    # Create data directories
    for vol in service.get("volumes", []):
        host_path = str(vol).split(":")[0]
        if host_path.startswith("/opt/") or host_path.startswith("/home/"):
            os.makedirs(host_path, exist_ok=True)

    # Stop the crash-looping containers
    try:
        subprocess.run(
            ["docker", "compose", "-f", compose_path, "-p", stack_name, "down"],
            capture_output=True, timeout=30,
        )
    except Exception:
        pass

    # Write fixed compose and redeploy
    with open(compose_path, "w") as f:
        f.write(new_yaml)

    try:
        result = subprocess.run(
            ["docker", "compose", "-f", compose_path, "-p", stack_name, "up", "-d"],
            capture_output=True, text=True, timeout=120,
        )
        if result.returncode != 0:
            return {
                "status": "self_heal_failed",
                "fixes_applied": fixes_applied,
                "error": result.stderr[-300:],
                "message": "Applied fixes but deploy still failed.",
            }
    except subprocess.TimeoutExpired:
        return {"status": "self_heal_timeout", "fixes_applied": fixes_applied}

    # Verify the fix worked
    time.sleep(10)
    try:
        containers = _docker().containers.list(
            filters={"label": f"com.docker.compose.project={stack_name}"}
        )
        if not containers:
            containers = [c for c in _docker().containers.list(all=True)
                         if stack_name.replace("-", "") in c.name.replace("-", "")]

        for c in containers:
            c.reload()
            if c.attrs.get("RestartCount", 0) > 2:
                return {
                    "status": "self_heal_failed",
                    "fixes_applied": fixes_applied,
                    "message": "Applied fixes but container still crash-looping.",
                }

        # Save the working compose as a local template for future use
        local_template_dir = "/templates/local"
        if os.path.isdir(local_template_dir):
            template_path = os.path.join(local_template_dir, "%s.yml" % stack_name)
            with open(template_path, "w") as f:
                f.write(new_yaml)
            log.info("Self-heal: saved working compose as template: %s" % template_path)

        host_ip = os.environ.get("HOST_IP", "192.168.29.10")
        port = ""
        for p in service.get("ports", []):
            port = str(p).split(":")[0]
            break

        return {
            "status": "healthy",
            "self_healed": True,
            "fixes_applied": fixes_applied,
            "containers": [c.name for c in containers if c.status == "running"],
            "url": "http://%s:%s" % (host_ip, port) if port else None,
            "message": "Fixed and redeployed: %s" % "; ".join(fixes_applied),
        }

    except Exception as e:
        return {
            "status": "self_heal_unknown",
            "fixes_applied": fixes_applied,
            "message": "Applied fixes but verification failed: %s" % e,
        }


def deploy_stack(args: dict) -> dict:
    """Deploy a compose stack from template or raw YAML."""
    stack_name = args.get("name")
    template_name = args.get("template")
    compose_yaml = args.get("compose_yaml")
    variables = args.get("vars", {})
    approved = args.get("approved", False)

    if not stack_name:
        raise ValueError("stack name required")

    resources = resource_guard.check_resources()
    if not resources["ok"]:
        return {
            "status": "refused",
            "reason": "; ".join(resources["errors"]),
            "memory_mb": resources["memory_mb"],
            "disk_gb": resources["disk_gb"],
        }

    rendered_yaml = None
    from_template = False

    if template_name:
        variables = secrets_mgr.resolve_secrets(stack_name, variables)
        rendered_yaml = templates.render_template(template_name, variables)
        if rendered_yaml:
            from_template = True

    # If no template found, try auto-search from Docker Hub / LinuxServer.io
    if not rendered_yaml and not compose_yaml:
        search_name = template_name or stack_name
        log.info(f"No template for '{search_name}', searching Docker Hub...")
        search_result = image_search.find_and_generate(search_name)

        if search_result["found"]:
            compose_yaml = search_result["compose_yaml"]
            log.info(f"Found image: {search_result['image']} (source: {search_result['source']})")

            if not approved:
                return {
                    "status": "approval_required",
                    "compose_yaml": compose_yaml,
                    "image": search_result["image"],
                    "source": search_result["source"],
                    "description": search_result["description"],
                    "message": f"Found {search_result['image']} ({search_result['source']}): {search_result['description'][:100]}. Deploy this?",
                    "require_approval": True,
                }
        else:
            return {
                "status": "not_found",
                "message": f"No template or Docker image found for '{search_name}'.",
                "available_templates": templates.list_templates(),
            }

    if compose_yaml and not from_template:
        if not approved:
            return {
                "status": "approval_required",
                "compose_yaml": compose_yaml,
                "message": f"No template for '{stack_name}'. Review this compose YAML and approve.",
                "require_approval": True,
            }
        rendered_yaml = compose_yaml

    if not rendered_yaml:
        return {
            "status": "error",
            "message": "Provide either a template name or compose_yaml",
            "available_templates": templates.list_templates(),
        }

    # Auto-allocate port if needed
    if "PORT" in variables:
        preferred = int(variables.get("PORT", 8010))
        actual_port = port_allocator.find_free_port(preferred)
        variables["PORT"] = str(actual_port)
        if from_template and template_name:
            rendered_yaml = templates.render_template(template_name, variables)

    deploy_dir = f"/data/stacks/{stack_name}"
    os.makedirs(deploy_dir, exist_ok=True)
    compose_path = os.path.join(deploy_dir, "docker-compose.yml")

    with open(compose_path, "w") as f:
        f.write(rendered_yaml)

    try:
        result = subprocess.run(
            ["docker", "compose", "-f", compose_path, "-p", stack_name, "up", "-d"],
            capture_output=True, text=True, timeout=120,
        )
        if result.returncode != 0:
            return {
                "status": "deploy_failed",
                "stderr": result.stderr[-500:],
                "stdout": result.stdout[-500:],
            }
    except subprocess.TimeoutExpired:
        return {"status": "deploy_timeout", "message": "Deploy timed out after 120s"}

    # Post-deploy health verification
    time.sleep(10)
    try:
        containers = _docker().containers.list(
            filters={"label": f"com.docker.compose.project={stack_name}"}
        )
        if not containers:
            containers = [c for c in _docker().containers.list(all=True)
                         if stack_name.replace("-", "") in c.name.replace("-", "")]

        crash_looping = []
        healthy = []
        for c in containers:
            c.reload()
            if c.attrs.get("RestartCount", 0) > 2:
                logs_text = c.logs(tail=20).decode("utf-8", errors="replace")
                crash_looping.append({"name": c.name, "logs": logs_text})
            elif c.status == "running":
                healthy.append(c.name)

        if crash_looping:
            # Self-healing: analyze logs and try to fix common issues
            fix_result = _try_self_heal(stack_name, crash_looping, rendered_yaml, compose_path, variables)
            if fix_result:
                return fix_result
            # Could not self-heal — report to user
            return {
                "status": "deployed_but_failing",
                "crash_looping": crash_looping,
                "offer_rollback": True,
                "message": "Containers deployed but crash-looping. Want me to roll back?",
            }

        port = variables.get("PORT", "")
        host_ip = os.environ.get("HOST_IP", "192.168.29.10")
        url = f"http://{host_ip}:{port}" if port else None

        return {
            "status": "healthy",
            "containers": healthy,
            "url": url,
            "warnings": resources.get("warnings", []),
            "from_template": from_template,
        }

    except Exception as e:
        return {
            "status": "deployed_unknown",
            "message": f"Deploy succeeded but health check failed: {e}",
        }
