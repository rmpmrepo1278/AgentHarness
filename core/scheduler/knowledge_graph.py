#!/usr/bin/env python3
"""knowledge_graph.py — Semantic Knowledge Graphing and RSS ingestion.

Runs as a background cron job. Ingests configured RSS feeds, summarizes
them using the local LLM to extract semantic links and knowledge graphs,
and stores them locally for RAG indexing.
"""

import os
import json
import time
import logging
import feedparser
from pathlib import Path
from datetime import datetime, timezone

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
log = logging.getLogger(__name__)

RSS_FEEDS = [
    "https://news.ycombinator.com/rss",
    "https://www.theverge.com/rss/index.xml"
]

def analyze_with_local_llm(title: str, summary: str) -> str:
    """Use the local Qwen LLM via the smart proxy to extract entities."""
    import httpx
    
    proxy_url = os.environ.get("OPENAI_BASE_URL", "http://192.168.29.10:8080/v1/chat/completions")
    api_key = os.environ.get("OPENAI_API_KEY", "dummy")
    
    prompt = f"""
    Analyze the following article and extract a semantic knowledge graph.
    Identify key entities (People, Organizations, Technologies) and their relationships.
    Format as a structured markdown document suitable for a RAG knowledge base.
    
    Title: {title}
    Summary: {summary}
    """
    
    try:
        resp = httpx.post(
            proxy_url,
            json={
                "model": "local",  # Force local model for privacy and cost
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": 1024,
                "temperature": 0.1
            },
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=60.0
        )
        if resp.status_code == 200:
            return resp.json()["choices"][0]["message"]["content"]
        else:
            log.warning(f"Local LLM failed with {resp.status_code}: {resp.text}")
            return ""
    except Exception as e:
        log.warning(f"Error calling local LLM: {e}")
        return ""

def main():
    data_dir = Path("/home/rohit/agentharness/data/knowledge_graph")
    data_dir.mkdir(parents=True, exist_ok=True)
    
    log.info("Starting knowledge graph RSS ingestion run...")
    
    for feed_url in RSS_FEEDS:
        log.info(f"Fetching {feed_url}...")
        feed = feedparser.parse(feed_url)
        
        # Process the top 3 articles to save time
        for entry in feed.entries[:3]:
            # Generate a safe filename
            safe_title = "".join(c if c.isalnum() else "_" for c in entry.title)[:50]
            out_file = data_dir / f"{safe_title}.md"
            
            if out_file.exists():
                log.info(f"Skipping already ingested article: {entry.title}")
                continue
                
            log.info(f"Analyzing article: {entry.title}")
            analysis = analyze_with_local_llm(entry.title, getattr(entry, 'summary', ''))
            
            if analysis:
                content = f"# {entry.title}\n\n**Source:** {entry.link}\n**Date:** {datetime.now(timezone.utc).isoformat()}\n\n## Semantic Graph Analysis\n\n{analysis}\n"
                out_file.write_text(content, encoding="utf-8")
                log.info(f"Saved analysis to {out_file}")
            
            # Rate limit the local LLM
            time.sleep(2)

if __name__ == "__main__":
    main()
