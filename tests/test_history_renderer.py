"""Tests for history_renderer.py"""
from __future__ import annotations
import io
import pytest


def _make_reports(n: int = 2) -> list[dict]:
    return [
        {
            "timestamp": f"2026-06-{i+1:02d} 10:00",
            "commit": f"abc{i:03d}",
            "verdict": "可提交",
            "high": i, "medium": 1, "low": 2,
            "elapsed": 2.5,
            "items": [],
        }
        for i in range(n)
    ]


def _make_hotspots() -> list[dict]:
    return [
        {"file": "src/store/user.ts", "count": 8, "max_risk": "high", "last_seen": "2026-06-09 10:00"},
        {"file": "src/request.ts",    "count": 5, "max_risk": "medium", "last_seen": "2026-06-08 10:00"},
    ]


def test_render_overview_contains_expected_columns():
    from history_renderer import render_overview
    try:
        from rich.console import Console
        buf = io.StringIO()
        console = Console(file=buf, no_color=True, highlight=False)
        render_overview(_make_reports(), console=console)
        output = buf.getvalue()
        assert "日期" in output or "时间" in output or "2026-06" in output
        assert "abc" in output  # commit hash
    except ImportError:
        pytest.skip("Rich not installed")


def test_render_hotspots_contains_file_names():
    from history_renderer import render_hotspots
    try:
        from rich.console import Console
        buf = io.StringIO()
        console = Console(file=buf, no_color=True, highlight=False)
        render_hotspots(_make_hotspots(), console=console)
        output = buf.getvalue()
        assert "user.ts" in output
        assert "request.ts" in output
    except ImportError:
        pytest.skip("Rich not installed")


def test_render_trend_no_crash():
    from history_renderer import render_trend
    trend = {"high": [3, 2, 1], "medium": [1, 2, 3], "low": [0, 1, 0]}
    try:
        from rich.console import Console
        buf = io.StringIO()
        console = Console(file=buf, no_color=True, highlight=False)
        render_trend(trend, console=console)
        # 只要不崩溃就行，sparkline 是 Unicode 块字符
        assert len(buf.getvalue()) > 0
    except ImportError:
        pytest.skip("Rich not installed")


def test_render_overview_empty_shows_hint():
    from history_renderer import render_overview
    try:
        from rich.console import Console
        buf = io.StringIO()
        console = Console(file=buf, no_color=True, highlight=False)
        render_overview([], console=console)
        assert "luna" in buf.getvalue().lower() or "记录" in buf.getvalue()
    except ImportError:
        pytest.skip("Rich not installed")
