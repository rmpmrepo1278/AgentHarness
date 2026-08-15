"""
Task Router — Intent-based classification + quality-gated local-to-cloud escalation.

Routes incoming requests to the optimal local Ollama model based on task intent,
then optionally escalates to cloud providers when local quality is insufficient.

Strategy: Local-First with Cloud Escalation
  1. Classify intent (PLANNING / CODING / EXECUTION / SIMPLE)
  2. Select optimal local model for that intent
  3. Call local model
  4. Quick quality gate (heuristic, no LLM call)
  5. If quality fails → re-route to cloud with same prompt
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from enum import Enum
from typing import List, Optional

from core.providers.base import Complexity


class TaskIntent(str, Enum):
    PLANNING = "planning"
    CODING = "coding"
    EXECUTION = "execution"
    SIMPLE = "simple"


# ── Keyword banks for intent classification ───────────────────────────────────

PLANNING_KEYWORDS: List[str] = [
    "design", "architect", "plan", "analyze", "debug", "compare", "research",
    "review", "assess", "evaluate", "strategy", "architecture", "design pattern",
    "trade-off", "tradeoff", "pros and cons", "recommendation", "roadmap",
    "system design", "data flow", "workflow", "structure", "blueprint",
    "diagram", "overview", "high-level", "approach", "solution",
    "audit", "examine", "investigate", "gaps", "redundan",
    "pricing", "market", "cost", "explain", "setup", "configuration",
    "explain how", "how is", "how does", "how to set",
]

CODING_KEYWORDS: List[str] = [
    "implement", "write code", "create", "build", "refactor", "code",
    "function", "class", "method", "fix", "generate code", "write a function",
    "write a class", "snippet", "script", "module", "library", "api",
    "endpoint", "handler", "interface", "type", "struct", "enum",
    "bug", "syntax", "compile", "lint", "test", "unit test", "integration",
    "def ", "javascript", "typescript", "python",
    "java", "go", "rust", "export", "import", "return",
    "help me write", "better prompt", "write a",
]

EXECUTION_KEYWORDS: List[str] = [
    "run", "test", "verify", "execute", "deploy", "check", "validate",
    "compile", "install", "build", "git", "commit", "push", "pull",
    "shell", "command", "terminal", "bash", "output", "result",
    "status", "health", "monitor", "measure", "benchmark",
    "pipeline", "confirm", "resolve", "fix", "end-to-end",
    "kick off", "run the", "run a",
]

# Hedging / evasion language — signals low quality in local responses
HEDGING_PATTERNS: List[str] = [
    r"\bi think\b", r"\bi believe\b", r"\bi suggest\b", r"\bi recommend\b",
    r"\bi'm not sure\b", r"\bi am not sure\b", r"\bi can't\b", r"\bi cannot\b",
    r"\bi don't know\b", r"\bi dont know\b", r"\bunable to\b",
    r"\bas an ai\b", r"\bas a language model\b",
    r"\bmaybe\b", r"\bperhaps\b", r"\byou could\b", r"\bi'm unable\b",
    r"\bcannot provide\b", r"\bcan't provide\b",
    r"\bnot possible to\b", r"\bi'm not certain\b",
]
_HEDGING_RE = re.compile("|".join(HEDGING_PATTERNS), re.IGNORECASE)


# ── Local model selection ─────────────────────────────────────────────────────

# Maps Ollama model name → proxy provider name
MODEL_TO_PROVIDER: dict[str, str] = {
    "llama3.2:3b":  "local",
    "gemma4:12b":   "local-gemma12b",
    "qwen3:8b":     "local-qwen8b",
    "qwen3:32b":    "local-qwen32b",
}

def local_provider_for_model(model: str) -> str:
    """Map an Ollama model name to its proxy provider name."""
    return MODEL_TO_PROVIDER.get(model, "local")

# Maps (intent, complexity) → Ollama model name
LOCAL_MODEL_MAP: dict[tuple[str, str], str] = {
    # PLANNING
    ("planning", "low"):    "llama3.2:3b",     # Simple Q&A, quick answers
    ("planning", "medium"): "gemma4:12b",      # Medium design tasks
    ("planning", "high"):   "gemma4:12b",      # Complex planning
    ("planning", "critical"): "qwen3:32b",     # Deep reasoning (rare)
    # CODING
    ("coding", "low"):      "qwen3:8b",        # Quick snippets, fixes
    ("coding", "medium"):   "qwen3:32b",       # Standard code — 32b for quality
    ("coding", "high"):     "qwen3:32b",       # Multi-function code
    ("coding", "critical"): "qwen3:32b",       # Large modules
    # EXECUTION
    ("execution", "low"):   "llama3.2:3b",     # Tool use, simple checks
    ("execution", "medium"): "llama3.2:3b",
    ("execution", "high"):   "qwen3:8b",
    ("execution", "critical"): "qwen3:8b",
    # SIMPLE (fallback)
    ("simple", "low"):      "llama3.2:3b",
    ("simple", "medium"):   "llama3.2:3b",
    ("simple", "high"):     "llama3.2:3b",
    ("simple", "critical"): "llama3.2:3b",
}

# Cloud providers to escalate to (in order) if local quality gate fails
CLOUD_ESCALATION_MAP: dict[str, list[str]] = {
    TaskIntent.PLANNING.value: ["groq", "cerebras", "owl", "sambanova"],
    TaskIntent.CODING.value:   ["groq", "owl", "cerebras", "sambanova"],
    TaskIntent.EXECUTION.value: ["groq", "owl"],  # Rarely needed
    TaskIntent.SIMPLE.value:   ["groq", "owl"],
}

# Tasks that should go straight to cloud (skip local)
def should_go_cloud_directly(intent: TaskIntent, complexity: Complexity) -> bool:
    """Return True if this combination is likely beyond local capabilities."""
    # Deepseek/Claude level planning
    if intent == TaskIntent.PLANNING and complexity == Complexity.CRITICAL:
        return True
    # Large multi-file refactor
    if intent == TaskIntent.CODING and complexity == Complexity.CRITICAL:
        return True
    return False


# ── Classification ───────────────────────────────────────────────────────────

def classify_intent(prompt: str, system: Optional[str] = None) -> TaskIntent:
    """Classify a request into a task intent.

    Uses keyword matching — no LLM call, ~1ms latency.
    """
    combined = f"{prompt or ''} {system or ''}".lower()

    # Score each intent by keyword matches (word-boundary aware to avoid
    # false positives like "capital" matching "class")
    scores: dict[str, int] = {
        TaskIntent.PLANNING.value: 0,
        TaskIntent.CODING.value: 0,
        TaskIntent.EXECUTION.value: 0,
    }

    for intent in [TaskIntent.PLANNING, TaskIntent.CODING, TaskIntent.EXECUTION]:
        if intent == TaskIntent.PLANNING:
            keywords = PLANNING_KEYWORDS
            base_weight = 2
        elif intent == TaskIntent.CODING:
            keywords = CODING_KEYWORDS
            base_weight = 2
        else:
            keywords = EXECUTION_KEYWORDS
            base_weight = 1

        for kw in keywords:
            if " " in kw:
                # Bigrams: substring match (they're already specific enough)
                if kw in combined:
                    scores[intent.value] += 2
            else:
                # Single words: word-boundary match
                if re.search(r'\b' + re.escape(kw) + r'\b', combined):
                    scores[intent.value] += base_weight

    # Pick the highest-scoring intent (min threshold to avoid false positives)
    best = max(scores, key=scores.get)
    if scores[best] >= 2:
        return TaskIntent(best)

    return TaskIntent.SIMPLE


def estimate_complexity(
    prompt: str,
    system: Optional[str] = None,
    intent: Optional[TaskIntent] = None,
) -> Complexity:
    """Estimate task complexity from prompt length + intent + structure."""
    word_count = len((prompt or "").split())
    system_len = len((system or "").split())

    # Base complexity from token estimate
    total_tokens = word_count + system_len
    if total_tokens < 20:
        base = Complexity.LOW
    elif total_tokens < 100:
        base = Complexity.MEDIUM
    else:
        base = Complexity.HIGH

    # Intent-based complexity adjustment
    if intent in (TaskIntent.PLANNING, TaskIntent.CODING):
        # Planning/coding tasks should never be routed to the smallest local
        # model even if the prompt is short — use at least MEDIUM so gemma4/qwen8b
        # gets selected instead of llama3.2:3b.
        if base == Complexity.LOW:
            base = Complexity.MEDIUM

    # Intent-based boosters for known-hard patterns
    if intent == TaskIntent.PLANNING:
        # Planning tasks that mention architecture/design → higher complexity
        if any(kw in (prompt or "").lower() for kw in
               ["architecture", "design", "system", "multi-module", "distributed"]):
            if base == Complexity.MEDIUM:
                base = Complexity.HIGH
            elif base == Complexity.HIGH:
                base = Complexity.CRITICAL
    elif intent == TaskIntent.CODING:
        # Multi-file or large implementation → higher complexity
        if any(kw in (prompt or "").lower() for kw in
               ["multi-file", "multiple files", "refactor", "rewrite", "full"]):
            if base == Complexity.LOW:
                base = Complexity.MEDIUM
            elif base == Complexity.MEDIUM:
                base = Complexity.HIGH

    return base


def select_local_model(intent: TaskIntent, complexity: Complexity) -> str:
    """Choose the best local Ollama model for this intent + complexity."""
    model = LOCAL_MODEL_MAP.get(
        (intent.value, complexity.value),
        "llama3.2:3b",
    )
    # Ensure model is available in local Ollama
    return model


# ── Quality Gate ──────────────────────────────────────────────────────────────

def check_quality(
    response_text: str,
    prompt: str,
    intent: TaskIntent,
) -> tuple[bool, str]:
    """Heuristic quality check on local model response.

    Returns (passed, reason). If passed=False, the caller should escalate
    to a cloud provider.

    These checks are fast (~1ms) and require no LLM calls.
    """
    text = response_text.strip()
    if not text:
        return False, "empty_response"

    # 1. Response is way too short (< 20% of prompt length)
    if len(text) < len(prompt.strip()) * 0.2 and len(prompt.strip()) > 10:
        return False, "too_short"

    # 2. Hedging / evasion language
    hedges = _HEDGING_RE.findall(text)
    if hedges:
        return False, f"hedging:{hedges[0]}"

    # 3. Intent-specific structural checks
    if intent == TaskIntent.CODING:
        # Should have code blocks or def/class for coding tasks
        has_code = bool(re.search(r"```|\bdef \w+|class \w+", text))
        prompt_mentions_code = bool(
            re.search(r"\b(function|class|method|implement|write code|script)\b",
                       prompt, re.IGNORECASE)
        )
        if prompt_mentions_code and not has_code:
            return False, "no_code_structure"

    if intent == TaskIntent.PLANNING:
        # Should have structured output (numbered lists, headers, bullet points)
        has_structure = bool(
            re.search(r"\n\s*(1\.|2\.|Step|##|\*\s|-|\d+\))", text)
        )
        if not has_structure and len(text) > 200:
            return False, "lacks_structure"

    # 4. For very short prompts, accept any non-empty response
    if len(prompt.strip().split()) < 15:
        return True, "short_prompt_ok"

    return True, "passed"


@dataclass
class RoutingDecision:
    """Full routing decision for a request."""
    intent: TaskIntent
    complexity: Complexity
    local_model: str
    local_provider: str
    cloud_providers: List[str]
    direct_to_cloud: bool

    @property
    def needs_escalation(self) -> bool:
        return not self.direct_to_cloud


def decide(prompt: str, system: Optional[str] = None) -> RoutingDecision:
    """Full routing decision: classify → estimate complexity → select models."""
    intent = classify_intent(prompt, system)
    complexity = estimate_complexity(prompt, system, intent)
    local_model = select_local_model(intent, complexity)
    local_provider = local_provider_for_model(local_model)
    direct_cloud = should_go_cloud_directly(intent, complexity)
    cloud_providers = CLOUD_ESCALATION_MAP[intent.value]

    return RoutingDecision(
        intent=intent,
        complexity=complexity,
        local_model=local_model,
        local_provider=local_provider,
        cloud_providers=cloud_providers,
        direct_to_cloud=direct_cloud,
    )
