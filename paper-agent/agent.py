#!/usr/bin/env python3
"""
Paper-Agent — Personalized Research Paper Digest

Fetches daily arXiv papers tailored to your research interests.
Can use Zotero library or local PDFs to build interest profile.
Sends daily digest via Telegram.

Environment variables:
    OPENAI_API_KEY or OLLAMA_BASE_URL - for paper summarization
    ZOTERO_USER_ID / ZOTERO_API_KEY - optional Zotero integration
    TELEGRAM_BOT_TOKEN / TELEGRAM_HOME_CHANNEL - for notifications
"""

from __future__ import annotations

import json
import os
import re
import urllib.request
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from pathlib import Path

DATA_DIR = Path(os.environ.get("PAPER_AGENT_DATA_DIR", "/data"))
PORT = int(os.environ.get("PAPER_AGENT_PORT", "7878"))


def log(msg: str):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] paper-agent: {msg}", flush=True)


def load_interest_profile() -> dict:
    """Load research interest profile from Zotero or local config."""
    profile_file = DATA_DIR / "interest_profile.json"
    if profile_file.exists():
        return json.loads(profile_file.read_text())

    # Default profile based on common AI/tech topics
    return {
        "categories": ["cs.AI", "cs.CL", "cs.LG", "cs.CV", "cs.NE"],
        "keywords": [
            "large language model", "LLM", "transformer", "attention",
            "reinforcement learning", "RLHF", "fine-tuning", "RAG",
            "agent", "multi-modal", "diffusion", "neural network",
            "natural language processing", "NLP", "computer vision",
            "robotics", "autonomous", "inference", "benchmark"
        ],
        "max_papers": 10,
        "language": "en"
    }


def fetch_arxiv_papers(categories: list[str], max_results: int = 50) -> list[dict]:
    """Fetch recent papers from arXiv API."""
    papers = []
    for cat in categories:
        query = f"cat:{cat}"
        url = f"https://export.arxiv.org/api/query?search_query={urllib.parse.quote(query)}&sortBy=submittedDate&sortOrder=descending&max_results={max_results // len(categories)}"

        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Paper-Agent/1.0"})
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = resp.read().decode("utf-8")

            root = ET.fromstring(data)
            ns = {"atom": "http://www.w3.org/2005/Atom"}

            for entry in root.findall("atom:entry", ns)[:max_results]:
                paper = {
                    "title": entry.find("atom:title", ns).text.strip().replace("\n", " ") if entry.find("atom:title", ns) is not None else "Unknown",
                    "authors": ", ".join(a.find("atom:name", ns).text for a in entry.findall("atom:author", ns)[:3]),
                    "summary": entry.find("atom:summary", ns).text.strip().replace("\n", " ")[:500] if entry.find("atom:summary", ns) is not None else "",
                    "published": entry.find("atom:published", ns).text if entry.find("atom:published", ns) is not None else "",
                    "link": entry.find("atom:id", ns).text if entry.find("atom:id", ns) is not None else "",
                    "categories": [c.get("term") for c in entry.findall("atom:category", ns)],
                }
                papers.append(paper)

        except Exception as e:
            log(f"Error fetching {cat}: {e}")

    return papers


def score_paper_relevance(paper: dict, profile: dict) -> float:
    """Score how relevant a paper is to the user's interests."""
    score = 0.0
    text = f"{paper['title']} {paper['summary']}".lower()

    # Keyword matching
    for keyword in profile.get("keywords", []):
        if keyword.lower() in text:
            score += 2.0

    # Category matching
    for cat in paper.get("categories", []):
        if cat in profile.get("categories", []):
            score += 3.0

    # Recency bonus (papers from last 2 days)
    try:
        pub_date = datetime.fromisoformat(paper["published"].replace("Z", "+00:00"))
        if datetime.now(pub_date.tzinfo) - pub_date < timedelta(days=2):
            score += 1.0
    except Exception:
        pass

    return score


def summarize_paper(paper: dict) -> str:
    """Generate a concise summary of a paper using local Ollama or OpenAI."""
    title = paper["title"]
    abstract = paper["summary"][:1000]

    prompt = f"""Summarize this research paper in 3-4 sentences. Focus on:
1. What problem does it solve?
2. What's the key approach/innovation?
3. What are the main results?

Title: {title}
Abstract: {abstract}

Summary:"""

    # Try Ollama first (local, free)
    ollama_url = os.environ.get("OLLAMA_BASE_URL", "")
    if ollama_url:
        try:
            payload = json.dumps({
                "model": "llama3.2:3b",
                "prompt": prompt,
                "stream": False,
                "options": {"num_predict": 200, "temperature": 0.3},
            }).encode()
            req = urllib.request.Request(
                f"{ollama_url}/api/generate",
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=60) as resp:
                data = json.loads(resp.read().decode())
                return data.get("response", "").strip()
        except Exception as e:
            log(f"Ollama summary failed: {e}")

    # Fallback: return truncated abstract
    return abstract[:300] + "..."


def send_telegram_digest(papers: list[dict], summaries: list[str]):
    """Send daily paper digest via Telegram."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.environ.get("TELEGRAM_HOME_CHANNEL", "")

    if not token or not chat_id:
        log("Telegram not configured — skipping notification")
        return

    date_str = datetime.now().strftime("%A, %B %d")
    message = f"📚 **Daily Paper Digest** — {date_str}\n\n"
    message += f"Found {len(papers)} relevant papers:\n\n"

    for i, (paper, summary) in enumerate(zip(papers, summaries), 1):
        title = paper["title"][:80]
        link = paper.get("link", "")
        message += f"**{i}. {title}**\n"
        message += f"{summary[:200]}\n"
        message += f"🔗 {link}\n\n"

    try:
        payload = json.dumps({
            "chat_id": chat_id,
            "text": message[:4000],  # Telegram limit
            "parse_mode": "Markdown",
            "disable_web_page_preview": True,
        }).encode()
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        urllib.request.urlopen(req, timeout=15)
        log(f"Sent digest with {len(papers)} papers to Telegram")
    except Exception as e:
        log(f"Telegram send failed: {e}")


def run_daily_digest():
    """Main daily paper digest workflow."""
    log("Starting daily paper digest")

    profile = load_interest_profile()
    log(f"Profile: {len(profile.get('categories', []))} categories, {len(profile.get('keywords', []))} keywords")

    # Fetch papers
    papers = fetch_arxiv_papers(profile.get("categories", ["cs.AI", "cs.CL", "cs.LG"]))
    log(f"Fetched {len(papers)} papers from arXiv")

    # Score and rank
    scored = [(paper, score_paper_relevance(paper, profile)) for paper in papers]
    scored.sort(key=lambda x: x[1], reverse=True)

    # Take top N most relevant
    max_papers = profile.get("max_papers", 10)
    top_papers = [p for p, s in scored[:max_papers] if s > 0]

    if not top_papers:
        log("No relevant papers found today")
        return

    log(f"Selected {len(top_papers)} relevant papers")

    # Summarize
    summaries = []
    for paper in top_papers:
        summary = summarize_paper(paper)
        summaries.append(summary)

    # Send digest
    send_telegram_digest(top_papers, summaries)

    # Save state
    state_file = DATA_DIR / "paper_agent_state.json"
    state = {
        "last_run": datetime.now().isoformat(),
        "papers_found": len(papers),
        "papers_relevant": len(top_papers),
        "top_titles": [p["title"][:80] for p in top_papers],
    }
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    state_file.write_text(json.dumps(state, indent=2))


if __name__ == "__main__":
    run_daily_digest()
