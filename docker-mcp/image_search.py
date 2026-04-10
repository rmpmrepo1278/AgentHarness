"""Search Docker Hub and LinuxServer.io for container images and generate compose templates.
When no local template exists, this module finds the right image and builds a compose YAML."""
import re
import logging
import requests

log = logging.getLogger("image_search")

# LinuxServer.io images have standardized compose patterns
LSIO_BASE = "https://fleet.linuxserver.io/api/v2"

# Common homelab port mappings (container_port -> typical host_port)
COMMON_PORTS = {
    "80": 8080, "443": 8443, "8080": 8080, "3000": 3000,
    "8443": 8443, "5000": 5000, "8081": 8081, "9090": 9090,
}


def search_docker_hub(query: str, limit: int = 5) -> list:
    """Search Docker Hub for images matching a query.
    Returns list of {name, description, stars, official, pulls}."""
    try:
        resp = requests.get(
            "https://hub.docker.com/v2/search/repositories/",
            params={"query": query, "page_size": limit},
            timeout=10,
        )
        resp.raise_for_status()
        results = []
        for r in resp.json().get("results", []):
            results.append({
                "name": r.get("repo_name", ""),
                "description": (r.get("short_description", "") or "")[:200],
                "stars": r.get("star_count", 0),
                "official": r.get("is_official", False),
                "pulls": r.get("pull_count", 0),
            })
        return results
    except Exception as e:
        log.warning("Docker Hub search failed: %s" % e)
        return []


def search_linuxserver(query: str) -> dict | None:
    """Search LinuxServer.io fleet for an image.
    Returns image info or None."""
    try:
        resp = requests.get("%s/images" % LSIO_BASE, timeout=10)
        resp.raise_for_status()
        images = resp.json().get("data", {}).get("repositories", [])

        query_lower = query.lower().replace("-", "").replace("_", "")
        for img in images:
            name = img.get("name", "").lower().replace("-", "").replace("_", "")
            if query_lower in name or name in query_lower:
                return {
                    "name": "lscr.io/linuxserver/%s" % img.get("name", ""),
                    "description": img.get("description", ""),
                    "category": img.get("category", ""),
                    "stars": img.get("stars", 0),
                    "linuxserver": True,
                }
        return None
    except Exception as e:
        log.debug("LinuxServer search failed: %s" % e)
        return None


def get_image_details(image_name: str) -> dict:
    """Get exposed ports and volumes from a Docker Hub image config."""
    # Parse image name
    parts = image_name.split("/")
    if len(parts) == 1:
        namespace, repo = "library", parts[0]
    else:
        namespace, repo = parts[0], parts[1]

    # Strip tag
    repo = repo.split(":")[0]

    try:
        # Get auth token
        token_resp = requests.get(
            "https://auth.docker.io/token",
            params={"service": "registry.docker.io", "scope": "repository:%s/%s:pull" % (namespace, repo)},
            timeout=10,
        )
        token = token_resp.json().get("token", "")

        # Get manifest to find config digest
        manifest_resp = requests.get(
            "https://registry-1.docker.io/v2/%s/%s/manifests/latest" % (namespace, repo),
            headers={
                "Authorization": "Bearer %s" % token,
                "Accept": "application/vnd.docker.distribution.manifest.v2+json",
            },
            timeout=10,
        )
        config_digest = manifest_resp.json().get("config", {}).get("digest", "")

        if not config_digest:
            return {"ports": [], "volumes": []}

        # Get config blob
        config_resp = requests.get(
            "https://registry-1.docker.io/v2/%s/%s/blobs/%s" % (namespace, repo, config_digest),
            headers={"Authorization": "Bearer %s" % token},
            timeout=10,
        )
        config = config_resp.json().get("config", config_resp.json().get("container_config", {}))

        ports = list((config.get("ExposedPorts") or {}).keys())
        volumes = list((config.get("Volumes") or {}).keys())

        return {"ports": ports, "volumes": volumes}

    except Exception as e:
        log.debug("Failed to get image details for %s: %s" % (image_name, e))
        return {"ports": [], "volumes": []}


def generate_compose(app_name: str, image: str, ports: list = None,
                     volumes: list = None, env_vars: dict = None) -> str:
    """Generate a docker-compose YAML for an app."""
    import port_allocator

    lines = ['version: "3.8"', "services:", "  %s:" % app_name.replace(" ", "-")]
    lines.append("    image: %s" % image)
    lines.append("    container_name: %s" % app_name.replace(" ", "-"))

    # Ports
    if ports:
        lines.append("    ports:")
        for p in ports:
            container_port = p.split("/")[0]
            host_port = COMMON_PORTS.get(container_port, int(container_port))
            # Find free port
            try:
                host_port = port_allocator.find_free_port(host_port)
            except Exception:
                pass
            lines.append('      - "${PORT:-%d}:%s"' % (host_port, container_port))

    # Volumes
    if volumes:
        lines.append("    volumes:")
        for v in volumes:
            safe_name = v.strip("/").replace("/", "-")
            lines.append("      - ${DATA_DIR:-/opt/%s}/%s:%s" % (app_name, safe_name, v))

    # Environment
    if env_vars:
        lines.append("    environment:")
        for k, v in env_vars.items():
            lines.append("      %s: ${%s:-%s}" % (k, k, v))

    lines.append("    restart: unless-stopped")
    lines.append("")

    return "\n".join(lines)


def find_and_generate(app_name: str) -> dict:
    """Search for an app and generate a compose template.

    Returns:
        {
            "found": True/False,
            "image": "image:tag",
            "source": "linuxserver" | "dockerhub" | "not_found",
            "compose_yaml": "...",
            "description": "...",
            "stars": N,
        }
    """
    app_clean = app_name.lower().strip().replace(" ", "-")

    # 1. Check LinuxServer.io first (curated, reliable)
    lsio = search_linuxserver(app_clean)
    if lsio:
        details = get_image_details(lsio["name"])
        compose = generate_compose(
            app_clean, "%s:latest" % lsio["name"],
            ports=details["ports"], volumes=details["volumes"],
        )
        return {
            "found": True,
            "image": "%s:latest" % lsio["name"],
            "source": "linuxserver",
            "compose_yaml": compose,
            "description": lsio["description"],
            "stars": lsio.get("stars", 0),
        }

    # 2. Search Docker Hub
    results = search_docker_hub(app_clean)
    if results:
        # Pick the best result: official > most stars > most pulls
        best = sorted(results, key=lambda r: (r["official"], r["stars"], r["pulls"]), reverse=True)[0]
        image_name = best["name"]
        if ":" not in image_name:
            image_name += ":latest"

        details = get_image_details(best["name"])
        compose = generate_compose(
            app_clean, image_name,
            ports=details["ports"], volumes=details["volumes"],
        )
        return {
            "found": True,
            "image": image_name,
            "source": "dockerhub",
            "compose_yaml": compose,
            "description": best["description"],
            "stars": best["stars"],
        }

    return {
        "found": False,
        "image": "",
        "source": "not_found",
        "compose_yaml": "",
        "description": "No image found for '%s'" % app_name,
        "stars": 0,
    }
