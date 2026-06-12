"""Tests for luna history subcommand"""
from __future__ import annotations
import json
from pathlib import Path
from click.testing import CliRunner


def _write_report(d: Path, ts: str, high: int = 0) -> None:
    safe_ts = ts.replace(":", "").replace(" ", "_")
    data = {
        "timestamp": ts, "commit": "abc123",
        "verdict": "可提交",
        "high": high, "medium": 0, "low": 0,
        "elapsed": 1.0, "items": [], "fix_candidates": [],
    }
    (d / f"{safe_ts}_report.json").write_text(json.dumps(data), encoding="utf-8")


def test_history_cmd_no_reports_prints_hint(tmp_path):
    from luna import cli
    runner = CliRunner()
    result = runner.invoke(cli, [
        "history", "--config", "nonexistent.yaml",
        "--reports-dir", str(tmp_path),  # 空目录，无报告
    ])
    assert result.exit_code == 0
    assert "luna" in result.output.lower() or "记录" in result.output


def test_history_cmd_shows_overview(tmp_path):
    from luna import cli
    _write_report(tmp_path, "2026-06-11 10:00", high=2)
    runner = CliRunner()
    result = runner.invoke(cli, [
        "history", "--config", "nonexistent.yaml",
        "--reports-dir", str(tmp_path),
    ])
    assert result.exit_code == 0


def test_history_cmd_trend_flag(tmp_path):
    from luna import cli
    _write_report(tmp_path, "2026-06-11 10:00", high=1)
    runner = CliRunner()
    result = runner.invoke(cli, [
        "history", "--config", "nonexistent.yaml",
        "--reports-dir", str(tmp_path), "--trend",
    ])
    assert result.exit_code == 0


def test_history_cmd_hotspots_flag(tmp_path):
    from luna import cli
    _write_report(tmp_path, "2026-06-11 10:00")
    runner = CliRunner()
    result = runner.invoke(cli, [
        "history", "--config", "nonexistent.yaml",
        "--reports-dir", str(tmp_path), "--hotspots",
    ])
    assert result.exit_code == 0
