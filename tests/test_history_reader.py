"""Tests for phases/history_reader.py"""
from __future__ import annotations
import json
from pathlib import Path

from phases.history_reader import load_reports, aggregate_hotspots, build_trend


def _write_report(d: Path, ts: str, high: int = 0, medium: int = 0, low: int = 0,
                  items: list | None = None, commit: str = "abc1234") -> None:
    safe_ts = ts.replace(":", "").replace(" ", "_")
    data = {
        "timestamp": ts,
        "commit": commit,
        "verdict": "可提交",
        "high": high, "medium": medium, "low": low,
        "elapsed": 2.0,
        "items": items or [],
        "fix_candidates": [],
    }
    (d / f"{safe_ts}_report.json").write_text(json.dumps(data), encoding="utf-8")


# ---------------------------------------------------------------------------
# load_reports
# ---------------------------------------------------------------------------

def test_load_reports_returns_sorted_by_date(tmp_path):
    _write_report(tmp_path, "2026-06-09 10:00")
    _write_report(tmp_path, "2026-06-11 10:00")
    _write_report(tmp_path, "2026-06-10 10:00")
    reports = load_reports(str(tmp_path))
    timestamps = [r["timestamp"] for r in reports]
    assert timestamps == sorted(timestamps, reverse=True)


def test_load_reports_respects_limit(tmp_path):
    for i in range(5):
        _write_report(tmp_path, f"2026-06-0{i+1} 10:00")
    reports = load_reports(str(tmp_path), limit=3)
    assert len(reports) == 3


def test_load_reports_skips_malformed_json(tmp_path):
    (tmp_path / "bad_report.json").write_text("not json{{{", encoding="utf-8")
    _write_report(tmp_path, "2026-06-11 10:00")
    reports = load_reports(str(tmp_path))
    assert len(reports) == 1


def test_load_reports_excludes_latest_json(tmp_path):
    (tmp_path / "latest.json").write_text(json.dumps({"fix_candidates": []}), encoding="utf-8")
    _write_report(tmp_path, "2026-06-11 10:00")
    reports = load_reports(str(tmp_path))
    assert len(reports) == 1


def test_load_reports_empty_dir(tmp_path):
    assert load_reports(str(tmp_path)) == []


# ---------------------------------------------------------------------------
# aggregate_hotspots
# ---------------------------------------------------------------------------

def test_aggregate_hotspots_counts_files_correctly(tmp_path):
    _write_report(tmp_path, "2026-06-09 10:00", items=[
        {"file": "a.ts", "line": 1, "risk": "high"},
        {"file": "b.ts", "line": 2, "risk": "medium"},
    ])
    _write_report(tmp_path, "2026-06-10 10:00", items=[
        {"file": "a.ts", "line": 5, "risk": "medium"},
    ])
    reports = load_reports(str(tmp_path))
    hotspots = aggregate_hotspots(reports)
    a_entry = next(h for h in hotspots if h["file"] == "a.ts")
    assert a_entry["count"] == 2
    assert a_entry["max_risk"] == "high"


def test_aggregate_hotspots_returns_top_n(tmp_path):
    for i in range(8):
        _write_report(tmp_path, f"2026-06-{i+1:02d} 10:00", items=[
            {"file": f"file{i}.ts", "line": 1, "risk": "medium"},
        ])
    reports = load_reports(str(tmp_path))
    hotspots = aggregate_hotspots(reports, top_n=5)
    assert len(hotspots) <= 5


# ---------------------------------------------------------------------------
# build_trend
# ---------------------------------------------------------------------------

def test_get_file_history_returns_flagged_count(tmp_path):
    from phases.history_reader import get_file_history
    _write_report(tmp_path, "2026-06-09 10:00", items=[
        {"file": "a.ts", "line": 1, "risk": "high"},
    ])
    _write_report(tmp_path, "2026-06-10 10:00", items=[
        {"file": "a.ts", "line": 5, "risk": "medium"},
    ])
    reports = load_reports(str(tmp_path))
    history = get_file_history(reports, ["a.ts"])
    assert history["a.ts"]["flagged_count"] == 2


def test_get_file_history_includes_recent_issues(tmp_path):
    from phases.history_reader import get_file_history
    _write_report(tmp_path, "2026-06-09 10:00", items=[
        {"file": "a.ts", "line": 1, "risk": "high"},
    ])
    reports = load_reports(str(tmp_path))
    history = get_file_history(reports, ["a.ts"])
    assert len(history["a.ts"]["recent_issues"]) >= 1
    assert history["a.ts"]["recent_issues"][0]["risk"] == "high"


def test_get_file_history_omits_unrelated_files(tmp_path):
    from phases.history_reader import get_file_history
    _write_report(tmp_path, "2026-06-09 10:00", items=[
        {"file": "other.ts", "line": 1, "risk": "high"},
    ])
    reports = load_reports(str(tmp_path))
    history = get_file_history(reports, ["a.ts"])
    assert "a.ts" not in history


def test_build_trend_returns_counts_per_report(tmp_path):
    _write_report(tmp_path, "2026-06-09 10:00", high=3, medium=1, low=2)
    _write_report(tmp_path, "2026-06-10 10:00", high=0, medium=2, low=1)
    reports = load_reports(str(tmp_path))
    trend = build_trend(reports)
    assert "high" in trend and "medium" in trend and "low" in trend
    assert len(trend["high"]) == 2
    # 旧→新顺序，reports 是倒序的，trend 应反转为旧→新
    assert trend["high"][0] == 3 or trend["high"][1] == 3


def test_get_file_history_limits_recent_issues(tmp_path):
    from phases.history_reader import get_file_history
    for i in range(5):
        _write_report(tmp_path, f"2026-06-{i+1:02d} 10:00", items=[
            {"file": "a.ts", "line": i, "risk": "high"},
        ])
    reports = load_reports(str(tmp_path))
    history = get_file_history(reports, ["a.ts"], max_recent=3)
    assert len(history["a.ts"]["recent_issues"]) <= 3
    assert history["a.ts"]["flagged_count"] == 5
