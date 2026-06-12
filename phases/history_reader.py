from __future__ import annotations
import json
from pathlib import Path


def load_reports(reports_dir: str, limit: int = 30) -> list[dict]:
    """扫描报告目录，返回按时间倒序排列的报告列表（跳过 latest.json 和解析失败的文件）。"""
    d = Path(reports_dir)
    if not d.exists():
        return []

    reports = []
    for f in d.glob("*_report.json"):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            if "timestamp" in data:
                reports.append(data)
        except (json.JSONDecodeError, OSError):
            continue

    reports.sort(key=lambda r: r["timestamp"], reverse=True)
    return reports[:limit]


def aggregate_hotspots(reports: list[dict], top_n: int = 10) -> list[dict]:
    """统计各文件在所有报告中的出现次数、最高风险、最近标记时间。"""
    _risk_order = {"high": 0, "medium": 1, "low": 2}
    file_stats: dict[str, dict] = {}

    for report in reports:
        ts = report.get("timestamp", "")
        for item in report.get("items", []):
            f = item.get("file", "")
            if not f:
                continue
            risk = item.get("risk", "low")
            if f not in file_stats:
                file_stats[f] = {"file": f, "count": 0, "max_risk": "low", "last_seen": ""}
            stats = file_stats[f]
            stats["count"] += 1
            if _risk_order.get(risk, 2) < _risk_order.get(stats["max_risk"], 2):
                stats["max_risk"] = risk
            if ts > stats["last_seen"]:
                stats["last_seen"] = ts

    sorted_stats = sorted(file_stats.values(), key=lambda s: s["count"], reverse=True)
    return sorted_stats[:top_n]


def get_file_history(
    reports: list[dict],
    files: list[str],
    max_recent: int = 3,
) -> dict:
    """
    给指定文件列表返回历史问题摘要，供 LLM 判断"这个文件是不是老问题"。
    返回格式：{file: {"flagged_count": N, "recent_issues": [{timestamp, risk, line}]}}
    只返回在 files 里且历史上出现过的文件。
    """
    file_set = set(files)
    result: dict[str, dict] = {}

    for report in reports:
        ts = report.get("timestamp", "")
        for item in report.get("items", []):
            f = item.get("file", "")
            if f not in file_set:
                continue
            if f not in result:
                result[f] = {"flagged_count": 0, "recent_issues": []}
            result[f]["flagged_count"] += 1
            if len(result[f]["recent_issues"]) < max_recent:
                result[f]["recent_issues"].append({
                    "timestamp": ts,
                    "risk": item.get("risk", "low"),
                    "line": item.get("line", 0),
                })

    return result


def build_trend(reports: list[dict]) -> dict:
    """返回各风险等级随时间的计数序列（旧→新）。"""
    # reports 是倒序（新→旧），反转为旧→新
    ordered = list(reversed(reports))
    return {
        "high":   [r.get("high", 0) for r in ordered],
        "medium": [r.get("medium", 0) for r in ordered],
        "low":    [r.get("low", 0) for r in ordered],
    }
