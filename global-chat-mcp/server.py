#!/usr/bin/env python3
"""Global Chat MCP - Communication services."""
from __future__ import annotations

import logging
import os
import sys

import requests

sys.path.insert(0, os.environ.get("MCP_BASE_DIR", "/mcp-base"))
from mcp_base import MCPServer

logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(name)s: %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
log = logging.getLogger("global-chat-mcp")

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "-1003976074764")

DISCORD_WEBHOOK = os.environ.get("DISCORD_WEBHOOK", "")
SLACK_WEBHOOK = os.environ.get("SLACK_WEBHOOK", "")

def send_telegram(args):
    message = args.get("message", "")
    chat_id = args.get("chat_id", TELEGRAM_CHAT_ID)
    parse_mode = args.get("parse_mode", "Markdown")
    disable_notification = args.get("disable_notification", False)
    if not message:
        return {"error": "message is required"}
    if not TELEGRAM_BOT_TOKEN:
        return {"error": "Telegram bot token not configured"}
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    data = {"chat_id": chat_id, "text": message, "parse_mode": parse_mode, "disable_notification": disable_notification}
    try:
        r = requests.post(url, json=data, timeout=10)
        return r.json()
    except Exception as e:
        return {"error": str(e)}

def send_discord(args):
    message = args.get("message", "")
    username = args.get("username", "Global Chat")
    avatar_url = args.get("avatar_url", "")
    if not message:
        return {"error": "message is required"}
    if not DISCORD_WEBHOOK:
        return {"error": "Discord webhook not configured"}
    data = {"content": message}
    if username: data["username"] = username
    if avatar_url: data["avatar_url"] = avatar_url
    try:
        r = requests.post(DISCORD_WEBHOOK, json=data, timeout=10)
        return {"status": "sent" if r.status_code == 204 else "error", "status_code": r.status_code}
    except Exception as e:
        return {"error": str(e)}

def send_slack(args):
    message = args.get("message", "")
    channel = args.get("channel", "")
    username = args.get("username", "Global Chat")
    icon_emoji = args.get("icon_emoji", ":robot_face:")
    if not message:
        return {"error": "message is required"}
    if not SLACK_WEBHOOK:
        return {"error": "Slack webhook not configured"}
    data = {"text": message, "username": username, "icon_emoji": icon_emoji}
    if channel: data["channel"] = channel
    try:
        r = requests.post(SLACK_WEBHOOK, json=data, timeout=10)
        return {"status": "sent" if r.status_code == 200 else "error", "status_code": r.status_code}
    except Exception as e:
        return {"error": str(e)}

def broadcast_all(args):
    message = args.get("message", "")
    priority = args.get("priority", "normal")
    if not message:
        return {"error": "message is required"}
    results = {}
    if TELEGRAM_BOT_TOKEN:
        results["telegram"] = send_telegram({"message": message})
    if DISCORD_WEBHOOK:
        results["discord"] = send_discord({"message": message})
    if SLACK_WEBHOOK:
        results["slack"] = send_slack({"message": message})
    return {"results": results, "sent_to": len(results)}

TOOL_SCHEMAS = [
    {"name": "send_telegram", "description": "Send a message via Telegram Bot API.",
        "inputSchema": {"type": "object", "properties": {"message": {"type": "string"}, "chat_id": {"type": "string", "default": "-1003976074764"}, "parse_mode": {"type": "string", "default": "Markdown"}, "disable_notification": {"type": "boolean", "default": False}}, "required": ["message"]}},
    {"name": "send_discord", "description": "Send a message via Discord webhook.",
        "inputSchema": {"type": "object", "properties": {"message": {"type": "string"}, "username": {"type": "string", "default": "Global Chat"}, "avatar_url": {"type": "string"}}, "required": ["message"]}},
    {"name": "send_slack", "description": "Send a message via Slack webhook.",
        "inputSchema": {"type": "object", "properties": {"message": {"type": "string"}, "channel": {"type": "string"}, "username": {"type": "string", "default": "Global Chat"}, "icon_emoji": {"type": "string", "default": ":robot_face:"}}, "required": ["message"]}},
    {"name": "broadcast_all", "description": "Send message to all configured channels.",
        "inputSchema": {"type": "object", "properties": {"message": {"type": "string"}, "priority": {"type": "string", "enum": ["normal", "high", "critical"], "default": "normal"}}, "required": ["message"]}},
]

def main():
    import logging
    logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(name)s: %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
    log = logging.getLogger("global-chat-mcp")
    import os
    port = int(os.environ.get("MCP_PORT", "8104"))
    s = MCPServer(name="global-chat-mcp", port=port, tools=TOOL_SCHEMAS)
    s.register_handler("send_telegram", send_telegram)
    s.register_handler("send_discord", send_discord)
    s.register_handler("send_slack", send_slack)
    s.register_handler("broadcast_all", broadcast_all)
    log.info(f"Global Chat MCP starting on :{port}")
    s.start()

if __name__ == "__main__":
    main()
