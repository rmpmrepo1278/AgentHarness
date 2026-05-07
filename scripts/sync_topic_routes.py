#!/usr/bin/env python3
"""
sync_topic_routes.py — Single source of truth sync for topic routing.

Reads topic_routes.json and writes channel_prompts into config.yaml.
Run this after any change to topic_routes.json, then restart the gateway.

Usage:
    python3 sync_topic_routes.py              # sync and show diff
    python3 sync_topic_routes.py --check      # verify in sync (exit 0) or not (exit 1)
    python3 sync_topic_routes.py --dry-run    # show what would change, don't write
"""

import json
import sys
import difflib
from pathlib import Path
from copy import deepcopy

import yaml

TOPIC_ROUTES = Path(__file__).resolve().parent.parent / "topic_routes.json"
CONFIG_YAML = Path(__file__).resolve().parent.parent / "config.yaml"


def load_routes() -> dict:
    with open(TOPIC_ROUTES) as f:
        return json.load(f)


def load_config() -> dict:
    with open(CONFIG_YAML) as f:
        return yaml.safe_load(f)


def build_channel_prompts(routes: dict) -> dict:
    """Build the channel_prompts dict from topic_routes.json."""
    prompts = routes.get("channel_prompts_by_thread_id", {})
    # Return sorted by thread ID for deterministic output
    return dict(sorted(prompts.items(), key=lambda x: int(x[0])))


def build_domain_summary(routes: dict) -> str:
    """Build a compact domain summary for SOUL.md Domain Awareness section."""
    thread_map = routes.get("topic_thread_ids", {})
    domain_info = routes.get("skills_by_domain", {})

    lines = []
    for thread_id, domain in sorted(thread_map.items(), key=lambda x: int(x[0])):
        info = domain_info.get(domain, {})
        focus = info.get("focus", [])
        tier = info.get("model_tier", "?")
        # Build a human-readable focus description
        focus_desc = ", ".join(f.replace("-", " ") for f in focus[:4])
        if len(focus) > 4:
            focus_desc += f" +{len(focus) - 4} more"
        lines.append(f'- **{domain}** (thread {thread_id}) → {tier} tier. {focus_desc or "general chat"}.')

    return "\n".join(lines)


def sync_config(config: dict, prompts: dict) -> dict:
    """Return a new config dict with channel_prompts synced."""
    new_config = deepcopy(config)
    if "telegram" not in new_config:
        new_config["telegram"] = {}
    if not isinstance(new_config["telegram"], dict):
        new_config["telegram"] = {}
    new_config["telegram"]["channel_prompts"] = prompts
    return new_config


def yaml_dump(config: dict) -> str:
    """Dump config to YAML string, preserving Hermes conventions."""

    def str_representer(dumper, data):
        # Use block scalar for multi-line strings
        if "\n" in data:
            return dumper.represent_scalar("tag:yaml.org,2002:str", data, style="|")
        return dumper.represent_scalar("tag:yaml.org,2002:str", data)

    yaml.add_representer(str, str_representer)
    return yaml.dump(
        config,
        default_flow_style=False,
        allow_unicode=True,
        sort_keys=False,
        width=120,
    )


def main():
    routes = load_routes()
    config = load_config()
    prompts = build_channel_prompts(routes)

    # --check mode: verify in sync
    if "--check" in sys.argv:
        current = config.get("telegram", {}).get("channel_prompts", {})
        if current == prompts:
            print("✓ topic_routes.json and config.yaml are in sync")
            sys.exit(0)
        else:
            print("✗ config.yaml is out of sync with topic_routes.json")
            print(f"  config.yaml has {len(current)} prompts, topic_routes.json has {len(prompts)}")
            sys.exit(1)

    new_config = sync_config(config, prompts)

    old_text = yaml_dump(config)
    new_text = yaml_dump(new_config)

    # --dry-run: show diff, don't write
    if "--dry-run" in sys.argv:
        diff = difflib.unified_diff(
            old_text.splitlines(keepends=True),
            new_text.splitlines(keepends=True),
            fromfile="config.yaml (before)",
            tofile="config.yaml (after)",
        )
        print("".join(diff))
        return

    # Show diff
    diff = list(difflib.unified_diff(
        old_text.splitlines(keepends=True),
        new_text.splitlines(keepends=True),
        fromfile="config.yaml (before)",
        tofile="config.yaml (after)",
    ))
    if diff:
        print("Changes:")
        print("".join(diff))
    else:
        print("No changes needed — config.yaml is already in sync.")

    # Write
    with open(CONFIG_YAML, "w") as f:
        f.write(new_text)
    print(f"\n✓ Wrote {len(prompts)} channel_prompts to {CONFIG_YAML}")

    # Print domain summary for convenience
    print("\nDomain summary (for SOUL.md):")
    print(build_domain_summary(routes))
    print("\nRestart gateway: systemctl --user restart hermes-gateway")


if __name__ == "__main__":
    main()
