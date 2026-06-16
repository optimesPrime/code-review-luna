from __future__ import annotations
import json
import dataclasses
import webbrowser
import threading
from collections import defaultdict
from pathlib import Path
from reporter import ReviewReport


_RISK_ORDER = {"high": 0, "medium": 1, "low": 2}


def _verdict(report: ReviewReport) -> str:
    all_items = list(report.blast_radius_items) + list(report.code_quality_items) + list(report.backend_review_items)
    if any(i.risk == "high" for i in all_items):
        return "critical"
    if any(i.risk == "medium" for i in all_items):
        return "needs_review"
    return "pass"


def _issue_mode(item) -> str:
    if getattr(item, "needs_human_review", False):
        return "manual"
    issue_type = getattr(item, "issue_type", "")
    if issue_type in ("missing_error_handling", "redundant", "dead_code"):
        return "auto"
    return "assist"


def _item_to_issue(item, idx: int) -> dict:
    reason = getattr(item, "reason", "") or getattr(item, "description", "")
    return {
        "id": idx,
        "risk": item.risk,
        "title": reason[:120],
        "file": item.file,
        "line": item.line,
        "issue_type": getattr(item, "issue_type", getattr(item, "category", "")),
        "reason": reason,
        "suggestion": getattr(item, "suggestion", "") or "",
        "mode": _issue_mode(item),
        "needs_human_review": bool(getattr(item, "needs_human_review", False)),
    }


def to_web_data(report: ReviewReport, branch: str, server_port: int) -> dict:
    all_items = sorted(
        list(report.blast_radius_items)
        + list(report.code_quality_items)
        + list(report.backend_review_items),
        key=lambda i: _RISK_ORDER.get(i.risk, 3),
    )
    issues = [_item_to_issue(item, i + 1) for i, item in enumerate(all_items)]
    return {
        "meta": {
            "branch": branch,
            "timestamp": report.timestamp,
            "verdict": _verdict(report),
            "server_port": server_port,
        },
        "issues": issues,
        "blast": {"clusters": build_blast_clusters(report)},
    }


def build_blast_clusters(report: ReviewReport) -> list:
    """Group impact_paths by source file into neural clusters for the DAG."""
    valid = [
        p for p in report.impact_paths
        if isinstance(p.get("path"), list) and len(p["path"]) >= 2
    ]
    if not valid:
        return []

    blast_by_file: dict = {}
    for item in report.blast_radius_items:
        blast_by_file.setdefault(item.file, item)

    symbol_names: dict = {}
    for s in report.changed_symbols:
        f = s.get("file", "")
        symbol_names[f] = s.get("name", "") or s.get("symbol", "") or f.split("/")[-1]

    by_source: dict = defaultdict(list)
    for p in valid:
        by_source[str(p["path"][0])].append(p)

    clusters = []
    for src_file, paths in by_source.items():
        seen_endpoints: dict = {}
        for p in paths:
            endpoint = str(p["path"][-1])
            if endpoint == src_file:
                continue
            existing = seen_endpoints.get(endpoint)
            if existing is None or _RISK_ORDER.get(p.get("risk", "low"), 3) < _RISK_ORDER.get(existing.get("risk", "low"), 3):
                seen_endpoints[endpoint] = p

        nodes = []
        for endpoint, p in seen_endpoints.items():
            blast_item = blast_by_file.get(endpoint)
            nodes.append({
                "file": endpoint,
                "line": blast_item.line if blast_item else 0,
                "risk": p.get("risk", "low"),
                "reason": (blast_item.reason if blast_item else "") or str(p.get("reason", "")),
            })
        nodes.sort(key=lambda n: _RISK_ORDER.get(n["risk"], 3))

        clusters.append({
            "source": {
                "file": src_file,
                "symbol": symbol_names.get(src_file, src_file.split("/")[-1]),
            },
            "nodes": nodes,
        })
    return clusters


def inject_and_write(template_path: str, data: dict, out_path: str) -> None:
    template = Path(template_path).read_text(encoding="utf-8")
    script_tag = (
        "<script>\n"
        f"window.__LUNA_DATA__ = {json.dumps(data, ensure_ascii=False, indent=2)};\n"
        "</script>"
    )
    html = template.replace("<!--LUNA_DATA_PLACEHOLDER-->", script_tag)
    Path(out_path).write_text(html, encoding="utf-8")


def generate_and_open(
    report: ReviewReport,
    out_dir: str,
    branch: str = "",
    quiet: bool = False,
) -> None:
    # deferred import to avoid circular dependency with web_server
    from web_server import LunaWebServer

    safe_ts = report.timestamp.replace(":", "").replace(" ", "_")
    out_path = str(Path(out_dir) / f"{safe_ts}_report.html")
    template_path = str(Path(__file__).parent / "web" / "report_template.html")

    if not Path(template_path).exists():
        return

    try:
        server = LunaWebServer(html_path=out_path, port=0)
        port = server.start()
        if not port:
            return

        data = to_web_data(report, branch=branch, server_port=port)
        inject_and_write(template_path, data, out_path)

        if not quiet:
            import sys
            print(f"\n🌐  Web 报告：http://localhost:{port}/  — 关闭标签页后自动退出\n", file=sys.stderr)
            webbrowser.open(f"http://localhost:{port}/")
    except Exception as _e:
        import traceback, sys
        print(f"\n[luna web] 内部错误: {_e}", file=sys.stderr)
        traceback.print_exc(file=sys.stderr)
