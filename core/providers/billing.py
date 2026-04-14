"""Gemini billing tracker — token-to-cost calculation with monthly persistence.

Reads daily token counts from BudgetTracker, applies Gemini 2.5 Flash-Lite pricing,
and maintains a monthly cumulative spend file at data/llm_billing.json.
"""

from __future__ import annotations

import datetime
import logging
from pathlib import Path
from typing import Any, Dict

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
        self._data: Dict[str, Any] = self._load()

    def _load(self) -> Dict[str, Any]:
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

    def update_from_budget(self, budget_data: Dict) -> None:
        """Recalculate today's cost from the live BudgetTracker data.

        Called on each billing query to stay in sync with the budget tracker.
        """
        today = _today()
        providers = budget_data.get("providers", {})

        for provider, stats in providers.items():
            if provider not in PRICING:
                continue
            tokens_in = stats.get("tokens_in", 0)
            tokens_out = stats.get("tokens_out", 0)
            cost = _calculate_cost(provider, tokens_in, tokens_out)

            # Store today's snapshot
            if today not in self._data["days"]:
                self._data["days"][today] = {}
            self._data["days"][today][provider] = {
                "tokens_in": tokens_in,
                "tokens_out": tokens_out,
                "cost_usd": round(cost, 6),
                "requests": stats.get("requests", 0),
            }

        # Recalculate monthly totals
        monthly = {}
        for day, day_providers in self._data["days"].items():
            for prov, pstats in day_providers.items():
                if prov not in monthly:
                    monthly[prov] = {"tokens_in": 0, "tokens_out": 0, "cost_usd": 0.0, "requests": 0}
                monthly[prov]["tokens_in"] += pstats["tokens_in"]
                monthly[prov]["tokens_out"] += pstats["tokens_out"]
                monthly[prov]["cost_usd"] += pstats["cost_usd"]
                monthly[prov]["requests"] += pstats["requests"]
        # Round monthly costs
        for prov in monthly:
            monthly[prov]["cost_usd"] = round(monthly[prov]["cost_usd"], 6)
        self._data["monthly_totals"] = monthly
        self._save()

    def get_billing_report(self, monthly_budget_usd: float = DEFAULT_MONTHLY_BUDGET_USD) -> Dict[str, Any]:
        """Return structured billing data for the API."""
        today = _today()
        today_data = self._data["days"].get(today, {})
        monthly = self._data["monthly_totals"]

        today_total = sum(p.get("cost_usd", 0) for p in today_data.values())
        month_total = sum(p.get("cost_usd", 0) for p in monthly.values())

        return {
            "month": self._data["month"],
            "date": today,
            "today": {
                "providers": today_data,
                "total_cost_usd": round(today_total, 6),
            },
            "month_to_date": {
                "providers": monthly,
                "total_cost_usd": round(month_total, 6),
                "days_tracked": len(self._data["days"]),
            },
            "budget": {
                "monthly_budget_usd": monthly_budget_usd,
                "remaining_usd": round(monthly_budget_usd - month_total, 6),
                "utilization_pct": round(month_total / monthly_budget_usd * 100, 1) if monthly_budget_usd > 0 else 0,
            },
            "pricing_reference": {
                "google_gemini_2.5_flash_lite": {
                    "input_per_1m_tokens": "$0.02",
                    "output_per_1m_tokens": "$0.10",
                }
            },
        }
