"""Gemini billing tracker — token-to-cost calculation with monthly persistence.

Reads daily token counts from BudgetTracker, applies Gemini 2.5 Flash-Lite pricing,
and maintains a monthly cumulative spend file at data/llm_billing.json.
"""

from __future__ import annotations

import datetime
import logging
from pathlib import Path
from typing import Any

from core.resilience.atomic_json import atomic_write_json, safe_read_json

logger = logging.getLogger(__name__)

# Gemini 2.5 Flash-Lite pricing (USD per 1M tokens, under 200K context)
# https://ai.google.dev/gemini-api/docs/pricing
PRICING = {
    "google": {
        "input_per_1m": 0.02,
        "output_per_1m": 0.10,
        "thinking_input_per_1m": 0.04,
    },
}

DEFAULT_MONTHLY_BUDGET_USD = 10.00


def _today() -> str:
    return datetime.date.today().isoformat()


def _this_month() -> str:
    return datetime.date.today().strftime("%Y-%m")


def _calculate_cost(provider: str, tokens_in: int, tokens_out: int) -> float:
    """Calculate USD cost for a provider given token counts."""
    rates = PRICING.get(provider)
    if not rates:
        return 0.0
    cost_in = tokens_in * rates["input_per_1m"] / 1_000_000
    cost_out = tokens_out * rates["output_per_1m"] / 1_000_000
    return cost_in + cost_out


class BillingTracker:
    """Track per-provider cost with daily and monthly accumulation."""

    def __init__(self, data_dir: str) -> None:
        self._path = Path(data_dir) / "llm_billing.json"
        self._data: dict[str, Any] = self._load()

    def _load(self) -> dict[str, Any]:
        default = {
            "month": _this_month(),
            "days": {},
            "monthly_totals": {},
        }
        data = safe_read_json(self._path, default=default)
        # Month rollover — archive and reset
        if data.get("month") != _this_month():
            logger.info("Billing month rollover: %s -> %s", data.get("month"), _this_month())
            data = {
                "month": _this_month(),
                "days": {},
                "monthly_totals": {},
            }
            atomic_write_json(self._path, data)
        return data

    def _save(self) -> None:
        atomic_write_json(self._path, self._data)


