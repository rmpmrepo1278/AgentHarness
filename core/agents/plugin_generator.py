"""Generate an agent plugin package for inbox watching and notification delivery.

Usage:
    agentharness generate-agent-plugin --agent chaguli --output ./plugin/
"""
from __future__ import annotations

import inspect
import logging
import os
import shutil
import stat
from pathlib import Path
from typing import Optional

from core.agents import inbox_watcher

log = logging.getLogger(__name__)

SYSTEMD_TEMPLATE = """[Unit]
Description=AgentHarness Inbox Watcher — delivers notifications via Telegram
After=network.target

[Service]
Type=simple
ExecStart=/usr/bin/python3 {watcher_path} --inbox-dir {inbox_dir}
EnvironmentFile={env_file}
Restart=on-failure
RestartSec=30

[Install]
WantedBy=multi-user.target
"""

INSTALL_INSTRUCTIONS = """# AgentHarness Inbox Watcher — Installation

## What This Does

This plugin watches AgentHarness's file-based inboxes (alerts, briefings,
proposals) and delivers notifications via YOUR agent's Telegram bot.

AgentHarness writes files. This watcher reads them and sends via Telegram.

## Quick Install

1. Copy the plugin directory to your agent's machine:
   ```
   scp -r {output_dir} user@agent-host:~/agentharness-plugin/
   ```

2. Set your Telegram credentials:
   ```
   export TELEGRAM_BOT_TOKEN="your-bot-token"
   export TELEGRAM_CHAT_ID="your-chat-id"
   ```

3. Test it:
   ```
   python3 inbox_watcher.py --inbox-dir {inbox_dir} --once
   ```

4. Run continuously:
   ```
   python3 inbox_watcher.py --inbox-dir {inbox_dir}
   ```

5. (Optional) Install as systemd service:
   ```
   sudo cp agentharness-inbox-watcher.service /etc/systemd/system/
   sudo systemctl enable agentharness-inbox-watcher
   sudo systemctl start agentharness-inbox-watcher
   ```

## For Chaguli Specifically

If your agent is Chaguli running in Docker, mount the AgentHarness data
directory into the container and run the watcher inside it:

```yaml
# docker-compose.yml addition:
volumes:
  - {inbox_dir}:/agentharness-data:ro
```

Then add to Chaguli's startup or run as a sidecar container.

## Verify It Works

```
agentharness test-agent-link
```
"""


def generate_plugin(
    output_dir: str,
    inbox_dir: str,
    agent_type: str = "generic",
    env_file: str = "",
) -> str:
    """Generate an agent plugin package.

    Returns the output directory path.
    """
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    # 1. Copy the inbox watcher script
    watcher_source = inspect.getfile(inbox_watcher)
    watcher_dest = out / "inbox_watcher.py"
    shutil.copy2(watcher_source, watcher_dest)
    watcher_dest.chmod(watcher_dest.stat().st_mode | stat.S_IEXEC)

    # 2. Generate systemd service file
    service_content = SYSTEMD_TEMPLATE.format(
        watcher_path=str(watcher_dest),
        inbox_dir=inbox_dir,
        env_file=env_file or str(out / ".env"),
    )
    (out / "agentharness-inbox-watcher.service").write_text(service_content)

    # 3. Generate .env template
    env_content = "# Agent's Telegram credentials (NOT AgentHarness's)\nTELEGRAM_BOT_TOKEN=\nTELEGRAM_CHAT_ID=\n"
    env_path = out / ".env"
    if not env_path.exists():
        env_path.write_text(env_content)

    # 4. Generate install instructions
    instructions = INSTALL_INSTRUCTIONS.format(
        output_dir=str(out),
        inbox_dir=inbox_dir,
    )
    (out / "INSTALL.md").write_text(instructions)

    log.info(f"Plugin generated: {out}")
    return str(out)
