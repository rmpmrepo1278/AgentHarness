#!/usr/bin/env python3
"""Hermes Memory MCP Server — exposes Hermes agent memory stores to Claude Code.

Provides read/write access to:
  - claudemem.db  (observations, session summaries, SOPs)
  - entities.db   (entity memory: people, companies, projects, topics)
  - shared_facts.db (cross-agent shared facts)
  - state.db      (session history + messages)

Runs as a standalone HTTP MCP server, registered behind the existing gateway
on port 8090. Claude Code connects via Streamable HTTP transport.
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any, Optional

sys.path.insert(0, os.environ.get("MCP_BASE_DIR", "/mcp-base"))
from mcp_base import MCPServer

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] hermes-memory-mcp: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("hermes-memory-mcp")

HERMES_HOME = Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes"))
SHARED_MEMORY_DIR = Path(os.environ.get("SHARED_MEMORY_DIR", Path.home() / "shared_agent_memory"))

DB_CLAUDEMEM = str(HERMES_HOME / "claudemem.db")
DB_ENTITIES = str(HERMES_HOME / "entities.db")
DB_SHARED_FACTS = str(SHARED_MEMORY_DIR / "shared_facts.db")
DB_STATE = str(HERMES_HOME / "state.db")
DB_UNIFIED = str(HERMES_HOME / "data" / "unified_memory.db")  # consolidated store

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _connect(db_path: str) -> sqlite3.Connection:
    """Open a SQLite connection with WAL mode and safe concurrency settings."""
    if not Path(db_path).exists():
        raise FileNotFoundError(f"Database not found: {db_path}")
    conn = sqlite3.connect(db_path, timeout=10, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def _rows_to_dicts(rows: list[sqlite3.Row]) -> list[dict]:
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Tool: hermes_recall — Search observations across all Hermes sessions
# ---------------------------------------------------------------------------

TOOL_RECALL = {
    "name": "hermes_recall",
    "title": "Hermes Recall",
    "description": (
        "Search Hermes agent's observation memory. Returns relevant past observations, "
        "decisions, and learned patterns. Use this to check what Hermes already knows "
        "about a topic before starting work. Supports both FTS keyword search and "
        "filtered queries by importance, date range, and category."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Search query — keywords or natural language. E.g., 'proxy config error', 'docker networking', 'career decision'.",
            },
            "limit": {
                "type": "integer",
                "description": "Max results (default 10, max 50).",
                "default": 10,
            },
            "min_importance": {
                "type": "number",
                "description": "Minimum importance score 0.0-1.0 (default 0.5). Higher = more important.",
                "default": 0.5,
            },
            "since_days": {
                "type": "integer",
                "description": "Only return observations from the last N days. Default 365 (1 year).",
                "default": 365,
            },
        },
        "required": ["query"],
    },
}


def handle_recall(args: dict) -> dict:
    query = args.get("query", "").strip()
    limit = min(int(args.get("limit", 10)), 50)
    min_importance = float(args.get("min_importance", 0.5))
    since_days = int(args.get("since_days", 365))

    if not query:
        return {"error": "query is required"}

    cutoff = time.time() - (since_days * 86400)
    conn = _connect(DB_CLAUDEMEM)
    try:
        # Layer 1: FTS5 full-text search
        # FTS5 works best with individual terms (AND is implicit)
        # Clean query: strip special FTS chars, split into terms, rejoin with spaces
        fts_query = " ".join(
            w.strip() for w in query.split()
            if w.strip() and w.strip() not in ("AND", "OR", "NOT", "NEAR")
        )
        if not fts_query:
            fts_query = query  # fallback

        try:
            fts_rows = conn.execute(
                """SELECT o.rowid, o.content, o.importance, o.timestamp, o.session_id,
                          snippet(observations_fts, 1, '<b>', '</b>', '...', 32) as snippet
                   FROM observations_fts
                   JOIN observations o ON o.rowid = observations_fts.rowid
                   WHERE observations_fts MATCH ?
                   AND o.importance >= ? AND o.timestamp >= ? AND o.compressed = 0
                   ORDER BY o.importance DESC, o.timestamp DESC
                   LIMIT ?""",
                (fts_query, min_importance, cutoff, limit),
            ).fetchall()
        except Exception:
            fts_rows = []

        if fts_rows:
            return {
                "results": _rows_to_dicts(fts_rows),
                "count": len(fts_rows),
                "source": "fts_search",
            }

        # Layer 2: Fallback — high-importance recent observations matching keywords
        keywords = query.lower().split()
        if keywords:
            clause = " OR ".join(["LOWER(content) LIKE ?"] * len(keywords))
            params = [f"%{k}%" for k in keywords] + [min_importance, cutoff, limit]
            fallback_rows = conn.execute(
                f"""SELECT rowid, content, importance, timestamp, session_id
                    FROM observations
                    WHERE ({clause})
                    AND importance >= ? AND timestamp >= ? AND compressed = 0
                    ORDER BY importance DESC, timestamp DESC
                    LIMIT ?""",
                params,
            ).fetchall()
            if fallback_rows:
                return {
                    "results": _rows_to_dicts(fallback_rows),
                    "count": len(fallback_rows),
                    "source": "keyword_fallback",
                }

        # Layer 3: Return most recent high-importance observations
        recent_rows = conn.execute(
            """SELECT rowid, content, importance, timestamp, session_id
               FROM observations
               WHERE importance >= ? AND timestamp >= ? AND compressed = 0
               ORDER BY timestamp DESC
               LIMIT ?""",
            (min_importance, cutoff, limit),
        ).fetchall()
        return {
            "results": _rows_to_dicts(recent_rows),
            "count": len(recent_rows),
            "source": "recent_fallback",
            "note": f"No matches for '{query}'. Showing recent high-importance observations.",
        }
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Tool: hermes_save_observation — Save a new observation
# ---------------------------------------------------------------------------

TOOL_SAVE = {
    "name": "hermes_save_observation",
    "title": "Hermes Save Observation",
    "description": (
        "Save an observation to Hermes's memory. This is the primary way to share "
        "knowledge between Claude Code and Hermes. What you save here, Hermes will "
        "recall in future conversations (and vice versa). "
        "Use importance: 0.3=casual, 0.5=notable, 0.7=important, 0.9=critical/SOP-worthy."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "content": {
                "type": "string",
                "description": "The observation text. Be specific and actionable. E.g., 'MCP gateway healthcheck fails when Docker bridge has stale ARP entries. Fix: restart docker-network-mcp container.'",
            },
            "importance": {
                "type": "number",
                "description": "Importance score 0.0-1.0 (default 0.7). 0.9=critical, 0.7=important, 0.5=notable, 0.3=casual.",
                "default": 0.7,
            },
            "category": {
                "type": "string",
                "description": "Category tag. E.g., 'debugging', 'config', 'decision', 'sop', 'infrastructure', 'career'.",
                "default": "context",
            },
            "source": {
                "type": "string",
                "description": "Source identifier. E.g., 'claude-code', 'hermes-telegram', 'manual'.",
                "default": "claude-code",
            },
        },
        "required": ["content"],
    },
}


def handle_save(args: dict) -> dict:
    content = args.get("content", "").strip()
    importance = float(args.get("importance", 0.7))
    category = args.get("category", "context")
    source = args.get("source", "claude-code")

    if not content:
        return {"error": "content is required"}
    if not 0.0 <= importance <= 1.0:
        return {"error": "importance must be between 0.0 and 1.0"}

    import uuid
    obs_id = str(uuid.uuid4())
    now = time.time()

    conn = _connect(DB_CLAUDEMEM)
    try:
        conn.execute(
            """INSERT INTO observations (id, session_id, timestamp, source, content, importance, compressed, category)
               VALUES (?, ?, ?, ?, ?, ?, 0, ?)""",
            (obs_id, f"cc-{obs_id[:8]}", now, source, content, importance, category),
        )
        conn.commit()
    finally:
        conn.close()

    # Dual-write into consolidated unified_memory.db (best-effort, never blocks save)
    try:
        uc = _connect(DB_UNIFIED)
        uc.execute(
            "INSERT INTO observations (id, session_id, timestamp, source, content, importance, category) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (obs_id, f"cc-{obs_id[:8]}", now, source, content, importance, category),
        )
        uc.commit()
        uc.close()
    except Exception as e:
        log.warning("unified dual-write failed: %s", e)

    return {
        "status": "saved",
        "id": obs_id,
        "importance": importance,
        "category": category,
    }


# ---------------------------------------------------------------------------
# Tool: hermes_session_summaries — Browse/search session summaries
# ---------------------------------------------------------------------------

TOOL_SESSIONS = {
    "name": "hermes_session_summaries",
    "title": "Hermes Session Summaries",
    "description": (
        "Browse or search Hermes agent's session summaries. Each summary captures "
        "the key topics, decisions, and open items from a conversation session. "
        "Use this to find past discussions about a specific topic."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Optional search query to filter sessions by topic.",
            },
            "limit": {
                "type": "integer",
                "description": "Max results (default 10, max 50).",
                "default": 10,
            },
        },
    },
}


def handle_sessions(args: dict) -> dict:
    query = args.get("query", "").strip()
    limit = min(int(args.get("limit", 10)), 50)

    conn = _connect(DB_CLAUDEMEM)
    try:
        if query:
            rows = conn.execute(
                """SELECT session_id, summary, topics, decisions_made, open_items, created_at
                   FROM session_summaries
                   WHERE summary LIKE ? OR topics LIKE ?
                   ORDER BY created_at DESC
                   LIMIT ?""",
                (f"%{query}%", f"%{query}%", limit),
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT session_id, summary, topics, decisions_made, open_items, created_at
                   FROM session_summaries
                   ORDER BY created_at DESC
                   LIMIT ?""",
                (limit,),
            ).fetchall()

        return {"sessions": _rows_to_dicts(rows), "count": len(rows)}
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Tool: hermes_sops — Search SOPs
# ---------------------------------------------------------------------------

TOOL_SOPS = {
    "name": "hermes_sops",
    "title": "Hermes SOPs",
    "description": (
        "Search Hermes agent's Standard Operating Procedures (SOPs). "
        "SOPs are step-by-step procedures for recurring tasks. "
        "Use this to find the correct procedure before performing a known task type."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Search query. E.g., 'restart proxy', 'deploy container', 'fix healthcheck'.",
            },
            "limit": {
                "type": "integer",
                "description": "Max results (default 5).",
                "default": 5,
            },
        },
    },
}


def handle_sops(args: dict) -> dict:
    query = args.get("query", "").strip()
    limit = min(int(args.get("limit", 5)), 20)

    conn = _connect(DB_CLAUDEMEM)
    try:
        if query:
            rows = conn.execute(
                """SELECT id, title, trigger_desc, steps, tags, use_count, success_count, version
                   FROM sops_fts
                   WHERE sops_fts MATCH ?
                   AND rowid IN (SELECT rowid FROM sops WHERE active = 1)
                   ORDER BY use_count DESC
                   LIMIT ?""",
                (query, limit),
            ).fetchall()
            if not rows:
                rows = conn.execute(
                    """SELECT id, title, trigger_desc, steps, tags, use_count, success_count, version
                       FROM sops
                       WHERE active = 1 AND (title LIKE ? OR trigger_desc LIKE ?)
                       ORDER BY use_count DESC
                       LIMIT ?""",
                    (f"%{query}%", f"%{query}%", limit),
                ).fetchall()
        else:
            rows = conn.execute(
                """SELECT id, title, trigger_desc, steps, tags, use_count, success_count, version
                   FROM sops
                   WHERE active = 1
                   ORDER BY use_count DESC
                   LIMIT ?""",
                (limit,),
            ).fetchall()

        return {"sops": _rows_to_dicts(rows), "count": len(rows)}
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Tool: hermes_entities — Look up entity memory
# ---------------------------------------------------------------------------

TOOL_ENTITIES = {
    "name": "hermes_entities",
    "title": "Hermes Entity Memory",
    "description": (
        "Look up entity memory — people, companies, projects, topics, and locations "
        "that Hermes has tracked. Includes relationships between entities and "
        "associated observations. Use this to get context on who/what is involved."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "Entity name to look up. E.g., 'Groq', 'Docker', 'career-ops'.",
            },
            "entity_type": {
                "type": "string",
                "description": "Filter by type: 'person', 'company', 'project', 'topic', 'location'.",
            },
            "include_observations": {
                "type": "boolean",
                "description": "Include associated observations (default true).",
                "default": True,
            },
            "limit": {
                "type": "integer",
                "description": "Max results (default 10).",
                "default": 10,
            },
        },
    },
}


def handle_entities(args: dict) -> dict:
    """Read from the consolidated unified_memory.db (entities.db was 0 bytes)."""
    name = args.get("name", "").strip()
    entity_type = args.get("entity_type", "").strip()
    include_obs = args.get("include_observations", True)
    limit = min(int(args.get("limit", 10)), 50)

    conn = _connect(DB_UNIFIED)
    try:
        conditions = []
        params = []
        if name:
            conditions.append("name LIKE ?")
            params.append(f"%{name}%")
        if entity_type:
            conditions.append("type = ?")
            params.append(entity_type)
        where = " AND ".join(conditions) if conditions else "1=1"
        params.append(limit)

        rows = conn.execute(
            "SELECT id, name, type, summary, created_at, updated_at "
            "FROM entities WHERE " + where + " ORDER BY updated_at DESC LIMIT ?",
            params,
        ).fetchall()

        entities = _rows_to_dicts(rows)
        if include_obs and entities:
            for ent in entities:
                ent["observations"] = _rows_to_dicts(
                    conn.execute(
                        "SELECT predicate, object, valid_at, confidence, source "
                        "FROM facts WHERE subject_id = ? AND invalid_at IS NULL "
                        "ORDER BY valid_at DESC LIMIT 20",
                        (ent["id"],),
                    ).fetchall()
                )
        return {"entities": entities, "count": len(entities)}
    finally:
        conn.close()

# Tool: hermes_shared_facts — Read cross-agent shared facts
# ---------------------------------------------------------------------------

TOOL_SHARED_FACTS = {
    "name": "hermes_shared_facts",
    "title": "Hermes Shared Facts",
    "description": (
        "Read cross-agent shared facts — knowledge that any agent in the system can access. "
        "These are high-confidence facts that have been promoted from individual observations. "
        "Use this to check established truths about the homelab, preferences, or decisions."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Search query. E.g., 'proxy', 'model routing', 'backup'.",
            },
            "category": {
                "type": "string",
                "description": "Filter by category. E.g., 'infrastructure', 'config', 'decision'.",
            },
            "min_confidence": {
                "type": "number",
                "description": "Minimum confidence 0.0-1.0 (default 0.7).",
                "default": 0.7,
            },
            "limit": {
                "type": "integer",
                "description": "Max results (default 20).",
                "default": 20,
            },
        },
    },
}


def handle_shared_facts(args: dict) -> dict:
    """Read shared facts from unified_memory.db (old shared_facts.db path did not exist)."""
    query = args.get("query", "").strip()
    min_confidence = float(args.get("min_confidence", 0.7))
    limit = min(int(args.get("limit", 20)), 100)

    conn = _connect(DB_UNIFIED)
    try:
        conditions = ["confidence >= ?"]
        params = [min_confidence]
        if query:
            ql = query.lower()
            conditions.append("(LOWER(object) LIKE ? OR LOWER(subject_id) LIKE ?)")
            params.append(f"%{ql}%")
            params.append(f"%{ql}%")
        where = " AND ".join(conditions)
        params.append(limit)
        rows = conn.execute(
            "SELECT subject_id, predicate, object, valid_at, source, confidence "
            "FROM facts WHERE " + where + " ORDER BY confidence DESC, valid_at DESC LIMIT ?",
            params,
        ).fetchall()
        return {"facts": _rows_to_dicts(rows), "count": len(rows)}
    finally:
        conn.close()

# Tool: hermes_add_shared_fact — Add a cross-agent shared fact
# ---------------------------------------------------------------------------

TOOL_ADD_SHARED_FACT = {
    "name": "hermes_add_shared_fact",
    "title": "Hermes Add Shared Fact",
    "description": (
        "Add a cross-agent shared fact. This is high-confidence knowledge that "
        "should be accessible to ALL agents (Hermes, Claude Code, sub-agents). "
        "Only add facts that are verified and broadly applicable."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "fact": {
                "type": "string",
                "description": "The fact text. Be specific. E.g., 'The LLM proxy routes Anthropic-format requests directly to Google Gemini 2.5 Pro, bypassing tiered routing.'",
            },
            "category": {
                "type": "string",
                "description": "Category tag. E.g., 'infrastructure', 'config', 'decision', 'preference'.",
                "default": "context",
            },
            "confidence": {
                "type": "number",
                "description": "Confidence score 0.0-1.0 (default 0.8). Only high-confidence facts should be shared.",
                "default": 0.8,
            },
            "source": {
                "type": "string",
                "description": "Source identifier. E.g., 'claude-code', 'hermes'.",
                "default": "claude-code",
            },
        },
        "required": ["fact"],
    },
}


def handle_add_shared_fact(args: dict) -> dict:
    fact = args.get("fact", "").strip()
    category = args.get("category", "context")
    confidence = float(args.get("confidence", 0.8))
    source = args.get("source", "claude-code")

    if not fact:
        return {"error": "fact is required"}

    conn = _connect(DB_SHARED_FACTS)
    try:
        cursor = conn.execute(
            """INSERT INTO shared_facts (fact, category, confidence, source, created_at, updated_at)
               VALUES (?, ?, ?, ?, datetime('now'), datetime('now'))""",
            (fact, category, confidence, source),
        )
        conn.commit()
        return {"status": "saved", "id": cursor.lastrowid, "confidence": confidence}
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Tool: hermes_active_sessions — List active/recent Claude Code sessions
# ---------------------------------------------------------------------------

TOOL_ACTIVE_SESSIONS = {
    "name": "hermes_active_sessions",
    "title": "Hermes Active Sessions",
    "description": (
        "List active or recent Claude Code sessions tracked in Hermes's session database. "
        "Shows session ID, title, message count, start time, and last activity. "
        "Use this to check what Claude Code is currently working on or find past sessions."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "active_only": {
                "type": "boolean",
                "description": "Show only currently active (not ended) sessions (default true).",
                "default": True,
            },
            "limit": {
                "type": "integer",
                "description": "Max results (default 20, max 100).",
                "default": 20,
            },
            "source": {
                "type": "string",
                "description": "Filter by source. E.g., 'acp' for Claude Code, 'telegram' for Hermes.",
            },
        },
    },
}


def handle_active_sessions(args: dict) -> dict:
    active_only = args.get("active_only", True)
    limit = min(int(args.get("limit", 20)), 100)
    source = args.get("source", "").strip()

    conn = _connect(DB_STATE)
    try:
        conditions = []
        params: list[Any] = []

        if active_only:
            conditions.append("ended_at IS NULL")
        if source:
            conditions.append("source = ?")
            params.append(source)

        where = " AND ".join(conditions) if conditions else "1=1"
        params.append(limit)

        rows = conn.execute(
            f"""SELECT s.id, s.source, s.title, s.message_count, s.tool_call_count,
                       s.started_at, s.ended_at, s.model,
                       (SELECT content FROM messages WHERE session_id = s.id ORDER BY timestamp DESC LIMIT 1) as last_message
                FROM sessions s
                WHERE {where}
                ORDER BY s.started_at DESC
                LIMIT ?""",
            params,
        ).fetchall()

        sessions = _rows_to_dicts(rows)
        # Convert timestamps to readable format
        for s in sessions:
            if s.get("started_at"):
                s["started_at_readable"] = time.strftime(
                    "%Y-%m-%d %H:%M:%S", time.localtime(s["started_at"])
                )
            if s.get("ended_at"):
                s["ended_at_readable"] = time.strftime(
                    "%Y-%m-%d %H:%M:%S", time.localtime(s["ended_at"])
                )

        return {"sessions": sessions, "count": len(sessions)}
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Tool: hermes_session_detail — Get full detail of a specific session
# ---------------------------------------------------------------------------

TOOL_SESSION_DETAIL = {
    "name": "hermes_session_detail",
    "title": "Hermes Session Detail",
    "description": (
        "Get the full detail of a specific session including all messages. "
        "Use this to understand the full reasoning trace of a past session."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "session_id": {
                "type": "string",
                "description": "The session ID to look up.",
            },
            "max_messages": {
                "type": "integer",
                "description": "Max messages to return (default 50). Messages are returned newest first.",
                "default": 50,
            },
        },
        "required": ["session_id"],
    },
}


def handle_session_detail(args: dict) -> dict:
    session_id = args.get("session_id", "").strip()
    max_messages = min(int(args.get("max_messages", 50)), 200)

    if not session_id:
        return {"error": "session_id is required"}

    conn = _connect(DB_STATE)
    try:
        session_row = conn.execute(
            """SELECT id, source, title, message_count, tool_call_count,
                       started_at, ended_at, model
                FROM sessions WHERE id = ?""",
            (session_id,),
        ).fetchone()

        if not session_row:
            return {"error": f"Session not found: {session_id}"}

        messages = conn.execute(
            """SELECT role, content, tool_name, timestamp, token_count
               FROM messages
               WHERE session_id = ?
               ORDER BY timestamp DESC
               LIMIT ?""",
            (session_id, max_messages),
        ).fetchall()

        result = dict(session_row)
        result["messages"] = _rows_to_dicts(list(reversed(messages)))
        return result
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# SOUL file resources
# ---------------------------------------------------------------------------

SOUL_FILES = {
    "soul://main": str(HERMES_HOME / "SOUL.md"),
    "soul://infra": str(HERMES_HOME / "SOUL_INFRA.md"),
    "soul://career": str(HERMES_HOME / "SOUL_CAREER.md"),
    "soul://knowledge": str(HERMES_HOME / "SOUL_KNOWLEDGE.md"),
    "soul://travel": str(HERMES_HOME / "SOUL_TRAVEL.md"),
}

TOOL_SOUL = {
    "name": "hermes_soul",
    "title": "Hermes SOUL Files",
    "description": (
        "Read Hermes agent's SOUL files — core identity, personality, and domain-specific "
        "behavioral guidelines. Available: soul://main (core identity), soul://infra "
        "(infrastructure domain), soul://career (career domain), soul://knowledge "
        "(knowledge domain), soul://travel (travel domain). "
        "Use this to understand Hermes's persona and domain expertise."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "soul_uri": {
                "type": "string",
                "description": "SOUL file URI. One of: soul://main, soul://infra, soul://career, soul://knowledge, soul://travel.",
                "enum": list(SOUL_FILES.keys()),
            },
        },
        "required": ["soul_uri"],
    },
}


def handle_soul(args: dict) -> dict:
    uri = args.get("soul_uri", "soul://main")
    path = SOUL_FILES.get(uri)
    if not path:
        return {"error": f"Unknown SOUL URI: {uri}. Available: {list(SOUL_FILES.keys())}"}

    try:
        content = Path(path).read_text(encoding="utf-8")
        return {"uri": uri, "path": path, "content": content}
    except FileNotFoundError:
        return {"error": f"SOUL file not found: {path}"}
    except Exception as e:
        return {"error": str(e)}


# ---------------------------------------------------------------------------
# Server setup
# ---------------------------------------------------------------------------

ALL_TOOLS = [
    TOOL_RECALL,
    TOOL_SAVE,
    TOOL_SESSIONS,
    TOOL_SOPS,
    TOOL_ENTITIES,
    TOOL_SHARED_FACTS,
    TOOL_ADD_SHARED_FACT,
    TOOL_ACTIVE_SESSIONS,
    TOOL_SESSION_DETAIL,
    TOOL_SOUL,
]

HANDLERS = {
    "hermes_recall": handle_recall,
    "hermes_save_observation": handle_save,
    "hermes_session_summaries": handle_sessions,
    "hermes_sops": handle_sops,
    "hermes_entities": handle_entities,
    "hermes_shared_facts": handle_shared_facts,
    "hermes_add_shared_fact": handle_add_shared_fact,
    "hermes_active_sessions": handle_active_sessions,
    "hermes_session_detail": handle_session_detail,
    "hermes_soul": handle_soul,
}


def main():
    port = int(os.environ.get("MCP_PORT", "8091"))
    server = MCPServer(name="hermes-memory", port=port, tools=ALL_TOOLS)

    for tool_name, handler in HANDLERS.items():
        server.register_handler(tool_name, handler)

    log.info("Hermes Memory MCP starting on port %d with %d tools", port, len(ALL_TOOLS))
    for t in ALL_TOOLS:
        log.info("  tool: %s", t["name"])

    server.start()


if __name__ == "__main__":
    main()
