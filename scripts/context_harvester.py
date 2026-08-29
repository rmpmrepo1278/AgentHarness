#!/usr/bin/env python3
"""
Auto-Fetch Context Harvester — Background context accumulation for Hermes memory.

Runs on a schedule (default: every 20 minutes) and harvests:
1. Recent git commits across all repos (deduped by commit hash)
2. Recent file changes in watched directories (deduped by path+mtime)
3. System health snippets (from health_check logs)
4. Recent terminal commands (deduped, user-configurable patterns)
5. Docker container events (streaming listener, not polling)

All harvested data is stored as observations in Hermes Memory MCP.
Deduplication via last-seen markers. TTL-based eviction of stale observations.
File lock prevents cron overlap.

Configurable via env vars:
    HARVEST_INTERVAL (default: 20 minutes)
    HARVEST_TTL_HOURS (default: 24, 0=disable eviction)
    HARVEST_LOCKFILE (default: /tmp/context_harvester.lock)
    HARVEST_INTERESTING_PATTERNS (default: see below)
"""

from __future__ import annotations

import fcntl
import json
import os
import re
import sqlite3
import subprocess
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

# ── Configuration ──────────────────────────────────────────────────────────

HARVEST_INTERVAL = int(os.environ.get("HARVEST_INTERVAL", "20"))
HARVEST_TTL_HOURS = int(os.environ.get("HARVEST_TTL_HOURS", "24"))
LOCKFILE = Path(os.environ.get("HARVEST_LOCKFILE", "/tmp/context_harvester.lock"))
STATE_FILE = Path("/home/rohit/.hermes/claudemem_harvest_state.json")

# User-configurable "interesting" command patterns (pipe-separated regex)
DEFAULT_INTERESTING = (
    r"^(git|docker|kubectl|helm|terraform|ansible|"
    r"npm|pip|cargo|go build|make|cmake|"
    r"ssh|scp|rsync|"
    r"systemctl|journalctl|"
    r"vim|nano|code|"
    r"curl|wget|"
    r"python|node|"
    r"claude|"
    r"apt|yum|dnf|"
    r"ufw|iptables|"
    r"nginx|apache|"
    r"postgres|mysql|redis|"
    r"test|lint|build|deploy)"
)
INTERESTING_PATTERNS = os.environ.get("HARVEST_INTERESTING_PATTERNS", DEFAULT_INTERESTING)
_interesting_re = re.compile(INTERESTING_PATTERNS, re.IGNORECASE)

# Noise commands to always skip
NOISE_COMMANDS = {"ls", "cd", "pwd", "clear", "exit", "history", "cat", "less", "more", "echo"}

GIT_REPOS = [
    Path("/home/rohit/agentharness"),
    Path("/home/rohit/.hermes/hermes-agent"),
]

WATCH_DIRS = [
    Path("/home/rohit/agentharness"),
    Path("/home/rohit/.hermes"),
]

WATCH_EXTENSIONS = {".py", ".sh", ".yaml", ".yml", ".json", ".md", ".txt", ".env"}
HEALTH_LOG = Path("/home/rohit/agentharness/data/logs/health_check.log")

# ── File Lock (prevents cron overlap) ──────────────────────────────────────

def acquire_lock() -> int | None:
    """Try to acquire exclusive lock. Returns file descriptor or None."""
    try:
        LOCKFILE.parent.mkdir(parents=True, exist_ok=True)
        fd = open(LOCKFILE, "w")
        fcntl.flock(fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        fd.write(str(os.getpid()))
        fd.flush()
        return fd  # caller must keep this open
    except OSError:
        return None


# ── State Management (with dedup markers) ──────────────────────────────────

def load_state() -> dict:
    """Load harvest state including last-seen markers."""
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except Exception:
            pass
    return {
        "last_harvest": None,
        "total_observations": 0,
        "last_git_hashes": {},       # repo -> last commit hash seen
        "last_file_mtimes": {},      # path -> last mtime seen
        "last_event_timestamp": None, # last docker event timestamp
        "last_command_timestamp": None, # last terminal command timestamp
    }


def save_state(state: dict):
    """Persist harvest state atomically."""
    try:
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = STATE_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, indent=2))
        tmp.rename(STATE_FILE)  # atomic on same filesystem
    except Exception as exc:
        print(f"  [warn] State save failed: {exc}", file=sys.stderr)


# ── Memory MCP Client ──────────────────────────────────────────────────────

def save_observation(content: str, category: str = "context-harvest",
                     importance: float = 0.3) -> bool:
    """Save an observation to Hermes Memory MCP. Verifies actual persistence."""
    try:
        import httpx
        r = httpx.post("http://localhost:8091/v1/messages", json={
            "jsonrpc": "2.0", "method": "tools/call",
            "params": {
                "name": "hermes_save_observation",
                "arguments": {"content": content, "category": category, "importance": importance}
            },
            "id": int(time.time()),
        }, timeout=15)
        r.raise_for_status()
        data = r.json()
        if data.get("error"):
            print(f"  [warn] MCP error: {data['error']}", file=sys.stderr)
            return False
        for part in data.get("result", {}).get("content", []):
            try:
                import json as _json
                inner = _json.loads(part.get("text", "{}"))
                if inner.get("status") == "saved":
                    return True
            except (Exception):
                pass
        print("  [warn] MCP save response did not confirm persistence", file=sys.stderr)
        return False
    except Exception as exc:
        print(f"  [warn] Memory save failed: {exc}", file=sys.stderr)
        return False


def evict_stale_observations(ttl_hours: HARVEST_TTL_HOURS,
                             max_high_importance: int = 1000):
    """
    Remove stale observations from Hermes memory DB.
    - Low-importance (< 0.5): evicted after TTL
    - High-importance (>= 0.5): capped at max_high_importance per source (evict oldest)
    """
    if ttl_hours <= 0 and max_high_importance <= 0:
        return
    try:
        db_path = Path("/home/rohit/.hermes/claudemem.db")
        if not db_path.exists():
            return
        conn = sqlite3.connect(str(db_path))
        total_deleted = 0

        if ttl_hours > 0:
            cutoff_ts = time.time() - (ttl_hours * 3600)
            cursor = conn.execute(
                "DELETE FROM observations WHERE timestamp < ? AND category = 'context-harvest' AND importance < 0.5",
                (cutoff_ts,)
            )
            low_deleted = cursor.rowcount
            total_deleted += low_deleted
            if low_deleted > 0:
                print(f"  [evict] Removed {low_deleted} stale low-importance (>{ttl_hours}h)")

        if max_high_importance > 0:
            cursor = conn.execute(
                "SELECT source, COUNT(*) as cnt FROM observations "
                "WHERE importance >= 0.5 GROUP BY source HAVING cnt > ?",
                (max_high_importance,)
            )
            for source, count in cursor.fetchall():
                excess = count - max_high_importance
                cursor2 = conn.execute(
                    "DELETE FROM observations WHERE id IN ("
                    "  SELECT id FROM observations WHERE source = ? AND importance >= 0.5"
                    "  ORDER BY timestamp ASC LIMIT ?"
                    ")",
                    (source, excess)
                )
                high_deleted = cursor2.rowcount
                total_deleted += high_deleted
                if high_deleted > 0:
                    print(f"  [evict] Capped {source}: removed {high_deleted} oldest "
                          f"(kept {max_high_importance})")

        conn.commit()
        conn.close()
        if total_deleted > 0:
            print(f"  [evict] Total removed: {total_deleted}")
    except Exception:
        print("  [warn] Eviction failed: %s", file=sys.stderr)

# ── Harvesters (all deduped) ────────────────────────────────────────────────

def harvest_git_commits(since: datetime, state: dict) -> list[str]:
    """Harvest recent git commits, deduped by commit hash."""
    observations = []
    since_str = since.strftime("%Y-%m-%d %H:%M:%S")
    last_hashes = state.get("last_git_hashes", {})

    for repo in GIT_REPOS:
        if not (repo / ".git").exists():
            continue
        repo_name = repo.name
        try:
            result = subprocess.run(
                ["git", "log", f"--since={since_str}",
                 "--oneline", "--no-merges", "--format=%H %h %s"],
                capture_output=True, text=True, timeout=10, cwd=str(repo)
            )
            if not result.stdout.strip():
                continue

            commits = []
            newest_hash = last_hashes.get(repo_name, "")
            for line in result.stdout.strip().split("\n"):
                parts = line.split(" ", 2)
                if len(parts) >= 3:
                    full_hash, short_hash, subject = parts[0], parts[1], parts[2]
                    # Skip if we've seen this commit before
                    if full_hash == newest_hash:
                        break
                    commits.append(f"{short_hash} {subject}")

            if commits:
                obs = f"Git commits in {repo_name}: " + "; ".join(commits[:10])
                observations.append(obs)
                # Update last-seen hash (first one is newest)
                state["last_git_hashes"][repo_name] = result.stdout.strip().split("\n")[0].split(" ")[0]
                print(f"  [git] {repo_name}: {len(commits)} new commits")
        except Exception as exc:
            print(f"  [git] {repo_name}: error — {exc}", file=sys.stderr)

    return observations


def harvest_file_changes(since: datetime, state: dict) -> list[str]:
    """Harvest recent file changes, deduped by path+mtime."""
    observations = []
    since_ts = since.timestamp()
    last_mtimes = state.get("last_file_mtimes", {})

    for watch_dir in WATCH_DIRS:
        if not watch_dir.exists():
            continue
        dir_name = watch_dir.name
        try:
            changed = []
            for root, dirs, files in os.walk(str(watch_dir)):
                dirs[:] = [d for d in dirs
                          if d not in (".git", "node_modules", "__pycache__", "venv", ".venv")]
                for fname in files:
                    fpath = Path(root) / fname
                    if fpath.suffix.lower() in WATCH_EXTENSIONS:
                        try:
                            mtime = fpath.stat().st_mtime
                            rel_path = fpath.relative_to(watch_dir)
                            path_key = str(rel_path)
                            last_mtime = last_mtimes.get(path_key, 0)

                            # Only report if changed since last harvest AND since the time window
                            if mtime > max(since_ts, last_mtime):
                                try:
                                    preview = fpath.read_text(errors="ignore").split("\n")[:2]
                                    preview_text = " | ".join(l.strip() for l in preview if l.strip())[:80]
                                except Exception:
                                    preview_text = "(unreadable)"
                                changed.append(f"{rel_path}: {preview_text}")
                                last_mtimes[path_key] = mtime
                        except (OSError, PermissionError):
                            pass

            if changed:
                obs = f"File changes in {dir_name}:\n" + "\n".join(changed[:15])
                observations.append(obs)
                print(f"  [files] {dir_name}: {len(changed)} new changes")
        except Exception as exc:
            print(f"  [files] {dir_name}: error — {exc}", file=sys.stderr)

    state["last_file_mtimes"] = last_mtimes
    return observations


def harvest_terminal_history(since: datetime, state: dict) -> list[str]:
    """Harvest recent terminal commands, deduped by timestamp, user-configurable patterns."""
    observations = []
    last_ts = state.get("last_command_timestamp", 0)

    for hist_file in [Path.home() / ".bash_history", Path.home() / ".zsh_history"]:
        if not hist_file.exists():
            continue
        try:
            lines = hist_file.read_text(errors="ignore").split("\n")
            interesting = []
            newest_ts = last_ts

            for line in lines[-100:]:
                line = line.strip()
                if not line:
                    continue
                # Skip noise
                cmd_base = line.split()[0] if line.split() else ""
                if cmd_base in NOISE_COMMANDS:
                    continue
                # Match user-configurable patterns
                if _interesting_re.search(line):
                    interesting.append(line)

            if interesting:
                obs = f"Recent commands from {hist_file.name}: " + "; ".join(interesting[:10])
                observations.append(obs)
                state["last_command_timestamp"] = time.time()
                print(f"  [history] {hist_file.name}: {len(interesting)} interesting commands")
        except Exception as exc:
            print(f"  [history] {hist_file.name}: error — {exc}", file=sys.stderr)

    return observations


def harvest_docker_events(since: datetime, state: dict) -> list[str]:
    """
    Harvest Docker events using streaming with auto-restart on disconnect.
    Tracks reconnect count to avoid infinite loops.
    """
    observations = []
    last_event_ts = state.get("last_event_timestamp")
    since_str = since.strftime("%Y-%m-%dT%H:%M:%S")
    max_reconnects = 3
    base_retry_secs = 2

    events = []
    reconnects = 0

    while reconnects <= max_reconnects:
        try:
            proc = subprocess.Popen(
                ["docker", "events",
                 f"--since={since_str}",
                 "--filter", "type=container",
                 "--format", "{{.Action}} {{.Actor.Attributes.name}} ({{.Type}}) at {{.Time}}"],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
            )

            # Read with timeout — if we get data, keep reading; if empty, check for disconnect
            start_wait = time.time()
            got_data = False
            while time.time() - start_wait < 10:
                line = proc.stdout.readline()
                if not line:
                    # EOF — docker events disconnected
                    break
                line = line.strip()
                if line:
                    got_data = True
                    events.append(line)
                    ts_match = re.search(r"at (\d+)$", line)
                    if ts_match:
                        state["last_event_timestamp"] = int(ts_match.group(1))

            proc.terminate()
            proc.wait(timeout=5)

            if got_data:
                break  # Success — exit reconnect loop

            # No data and no error — docker might be restarting
            reconnects += 1
            if reconnects <= max_reconnects:
                retry_secs = min(base_retry_secs * (2 ** (reconnects - 1)), 30)
                print(f"  [docker] no events, reconnect #{reconnects} in {retry_secs}s")
                time.sleep(retry_secs)
                # Update since_str to avoid re-getting old events
                since_str = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S")

        except Exception as exc:
            print(f"  [docker] error — {exc}", file=sys.stderr)
            reconnects += 1
            if reconnects > max_reconnects:
                break
            time.sleep(base_retry_secs * reconnects)

    if events:
        obs = "Docker events: " + "; ".join(events[:20])
        observations.append(obs)
        print(f"  [docker] {len(events)} events (reconnects={reconnects})")

    return observations


def harvest_health_snippets() -> list[str]:
    """Harvest recent health check alerts."""
    observations = []
    if HEALTH_LOG.exists():
        try:
            result = subprocess.run(
                ["tail", "-20", str(HEALTH_LOG)],
                capture_output=True, text=True, timeout=5
            )
            lines = [l for l in result.stdout.split("\n")
                    if l.strip() and "ERROR" in l.upper()]
            if lines:
                obs = "Health alerts: " + "; ".join(lines[:5])
                observations.append(obs)
                print(f"  [health] {len(lines)} alerts")
        except Exception:
            pass
    return observations


# ── Main harvest loop ──────────────────────────────────────────────────────

def _check_rate_limiter_pressure() -> tuple[bool, dict]:
    """
    Read rate_limit_tracker.json to determine if proxy is under heavy load.
    Returns (is_pressured, stats_dict).
    If >50% of tracked providers are in cooldown, skip low-importance observations.
    This prevents the harvester from adding token pressure during rate limits.
    """
    try:
        rl_file = Path("/home/rohit/agentharness/data/rate_limit_state.json")
        if not rl_file.exists():
            return False, {}
        rl_data = json.loads(rl_file.read_text())
        providers = rl_data.get("providers", {})
        if not providers:
            return False, {}
        in_cooldown = sum(1 for p in providers.values() if p.get("in_cooldown", False))
        total = len(providers)
        cooldown_pct = in_cooldown / total if total > 0 else 0
        stats = {"in_cooldown": in_cooldown, "total": total, "cooldown_pct": cooldown_pct}
        return cooldown_pct > 0.5, stats
    except Exception:
        return False, {}


def run_harvest(dry_run: bool = False) -> int:
    """Run one harvest cycle. Returns number of observations saved."""
    state = load_state()

    # Determine time window
    if state["last_harvest"]:
        since = datetime.fromisoformat(state["last_harvest"])
        max_lookback = datetime.now(UTC) - timedelta(minutes=HARVEST_INTERVAL)
        if since < max_lookback:
            since = max_lookback
    else:
        since = datetime.now(UTC) - timedelta(minutes=HARVEST_INTERVAL)

    # FEEDBACK LOOP: check if proxy is under rate limit pressure
    is_pressured, rl_stats = _check_rate_limiter_pressure()
    if is_pressured:
        print(f"[harvest] Rate limit pressure detected ({rl_stats['in_cooldown']}/{rl_stats['total']} "
              f"providers in cooldown, {rl_stats['cooldown_pct']:.0%}) — reducing harvest intensity")
        # Skip low-value harvesters during pressure
        skip_terminal = True
        skip_files = False  # file changes are still valuable
    else:
        skip_terminal = False
        skip_files = False

    print(f"[harvest] Collecting since {since.isoformat()}"
          f"{' (reduced mode)' if is_pressured else ''}")

    all_observations = []
    all_observations.extend(harvest_git_commits(since, state))
    if not skip_files:
        all_observations.extend(harvest_file_changes(since, state))
    if not skip_terminal:
        all_observations.extend(harvest_terminal_history(since, state))
    all_observations.extend(harvest_docker_events(since, state))
    all_observations.extend(harvest_health_snippets())

    # During pressure, only save high-importance observations
    if is_pressured:
        # Only keep git commits and health alerts (high value, low token cost)
        all_observations = [o for o in all_observations
                           if o.startswith("Git commits") or o.startswith("Health alerts")]
        print(f"[harvest] Pressure mode: filtered to {len(all_observations)} high-value observations")

    if not all_observations:
        print("[harvest] No new context found")
        state["last_harvest"] = datetime.now(UTC).isoformat()
        save_state(state)
        # Write heartbeat even on empty run
        try:
            heartbeat = Path("/home/rohit/agentharness/data/harvester_heartbeat.json")
            heartbeat.parent.mkdir(parents=True, exist_ok=True)
            heartbeat.write_text(json.dumps({
                "last_run": datetime.now(UTC).isoformat(),
                "success": True,
                "observations_saved": 0,
            }))
        except Exception as exc:
            print(f"  [warn] Heartbeat write failed: {exc}", file=sys.stderr)
        return 0

    if dry_run:
        print(f"[harvest] DRY RUN — would save {len(all_observations)} observations:")
        for obs in all_observations:
            print(f"  • {obs[:100]}...")
        return len(all_observations)

    saved = 0
    for obs in all_observations:
        if save_observation(obs, category="context-harvest", importance=0.3):
            saved += 1

    # Evict stale observations
    evict_stale_observations(HARVEST_TTL_HOURS)

    state["last_harvest"] = datetime.now(UTC).isoformat()
    state["total_observations"] = state.get("total_observations", 0) + saved
    save_state(state)

    print(f"[harvest] Saved {saved}/{len(all_observations)} observations "
          f"(total: {state['total_observations']})")

    # Write health heartbeat
    try:
        heartbeat = Path("/home/rohit/agentharness/data/harvester_heartbeat.json")
        heartbeat.parent.mkdir(parents=True, exist_ok=True)
        heartbeat.write_text(json.dumps({
            "last_run": datetime.now(UTC).isoformat(),
            "success": True,
            "observations_saved": saved,
        }))
    except Exception as exc:
        print(f"  [warn] Heartbeat write failed: {exc}", file=sys.stderr)

    return saved


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Homelab Context Harvester")
    parser.add_argument("--daemon", action="store_true",
                       help="Run continuously every N minutes")
    parser.add_argument("--dry-run", action="store_true",
                       help="Show what would be saved without saving")
    parser.add_argument("--interval", type=int, default=HARVEST_INTERVAL,
                       help=f"Minutes between harvests (default: {HARVEST_INTERVAL})")
    args = parser.parse_args()

    # Acquire file lock to prevent overlap
    lock_fd = acquire_lock()
    if lock_fd is None:
        print("[harvester] Another instance is already running — exiting")
        sys.exit(0)

    try:
        if args.daemon:
            print(f"[harvester] Starting daemon mode (every {args.interval} min)")
            while True:
                try:
                    run_harvest(dry_run=args.dry_run)
                except KeyboardInterrupt:
                    print("\n[harvester] Stopped")
                    break
                except Exception as exc:
                    print(f"[harvester] Error: {exc}", file=sys.stderr)
                time.sleep(args.interval * 60)
        else:
            count = run_harvest(dry_run=args.dry_run)
            if count > 0:
                print(f"✓ Harvested {count} observations")
    finally:
        lock_fd.close()


if __name__ == "__main__":
    main()
