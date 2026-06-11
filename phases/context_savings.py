from __future__ import annotations

import json
from typing import Any

CHARS_PER_TOKEN = 4


def estimate_tokens(obj: Any) -> int:
    """Estimate token count using 4 chars/token approximation."""
    if obj is None:
        return 0
    text = obj if isinstance(obj, str) else json.dumps(
        obj, default=str, ensure_ascii=True, separators=(",", ":"), sort_keys=True
    )
    if not text:
        return 0
    return max(1, (len(text) + CHARS_PER_TOKEN - 1) // CHARS_PER_TOKEN)


def estimate_diff_tokens(diff: str) -> int:
    """Estimate tokens for a raw diff string."""
    return estimate_tokens(diff)


def build_savings_summary(baseline_tokens: int, used_tokens: int) -> dict:
    saved = max(0, baseline_tokens - used_tokens)
    percent = round((saved / baseline_tokens) * 100) if baseline_tokens > 0 else 0
    return {
        "baseline": baseline_tokens,
        "used": used_tokens,
        "saved": saved,
        "saved_percent": percent,
    }
