from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import Path
from phases.blast_radius import BlastRadiusItem
from phases.code_quality import CodeQualityItem
from test_importer import TestCase
from phases.backend_models import BackendReviewItem


@dataclass
class ReviewReport:
    timestamp: str
    diff_summary: str
    blast_radius_items: list[BlastRadiusItem] = field(default_factory=list)
    code_quality_items: list[CodeQualityItem] = field(default_factory=list)
    related_tests: list[TestCase] = field(default_factory=list)
    backend_review_items: list[BackendReviewItem] = field(default_factory=list)
    skill_errors: list = field(default_factory=list)
    applied_fixes: list[str] = field(default_factory=list)
    skipped_items: list[str] = field(default_factory=list)
    fix_candidates: list = field(default_factory=list)
    impact_paths: list = field(default_factory=list)
    changed_symbols: list = field(default_factory=list)
    review_questions: list[str] = field(default_factory=list)
    token_savings: dict = field(default_factory=dict)
    migration_items: list = field(default_factory=list)
    api_change_items: list = field(default_factory=list)


def _backend_section(items: list[BackendReviewItem]) -> str:
    if not items:
        return "_未发现后端专项风险_"
    risk_order = {"high": 0, "medium": 1, "low": 2}
    lines = []
    for item in sorted(items, key=lambda x: risk_order.get(x.risk, 9)):
        note = " *(需人工确认)*" if item.needs_human_review else ""
        lines.append(
            f"### `{item.symbol}` -> `{item.file}:{item.line}`\n"
            f"- 分类: {item.category} | 风险: **{item.risk}** | 置信度: {item.confidence}{note}\n"
            f"- 原因: {item.reason}\n"
            f"- 证据: {item.evidence}\n"
            + (f"- 建议: {item.suggestion}\n" if item.suggestion else "")
        )
    return "\n".join(lines)


def _blast_section(items: list[BlastRadiusItem]) -> str:
    if not items:
        return "_未发现爆炸范围影响_"
    risk_order = {"high": 0, "medium": 1, "low": 2}
    lines = []
    for item in sorted(items, key=lambda x: risk_order.get(x.risk, 9)):
        note = " *(需人工确认)*" if item.needs_human_review else ""
        lines.append(
            f"### `{item.symbol}` → `{item.file}:{item.line}`\n"
            f"- 风险: **{item.risk}** | 置信度: {item.confidence}{note}\n"
            f"- 原因: {item.reason}\n"
            + (f"- 建议: {item.suggestion}\n" if item.suggestion else "")
        )
    return "\n".join(lines)


def _quality_section(items: list[CodeQualityItem]) -> str:
    if not items:
        return "_未发现代码质量问题_"
    lines = []
    for item in items:
        lines.append(
            f"### `{item.file}:{item.line}` — {item.description}\n"
            f"- 类型: {item.issue_type} | 风险: **{item.risk}** | 置信度: {item.confidence}\n"
            f"- 依据: {item.evidence}\n"
            + (f"- 建议: {item.suggestion}\n" if item.suggestion else "")
        )
    return "\n".join(lines)


def _tests_section(tests: list[TestCase]) -> str:
    if not tests:
        return "_未导入测试用例或无关联用例_"
    return "\n".join(
        f"- `{tc.file}:{tc.line}` — {tc.describe} > {tc.it}"
        for tc in tests
    )


def render(report: ReviewReport) -> str:
    return f"""# 代码审查报告 · {report.timestamp}

## 一、改动概述

{report.diff_summary}

## 二、后端审查

{_backend_section(report.backend_review_items)}

## 三、爆炸范围分析

{_blast_section(report.blast_radius_items)}

## 四、代码质量问题

{_quality_section(report.code_quality_items)}

## 五、关联测试用例

{_tests_section(report.related_tests)}

## 六、审查结论

> 由人工复审填写

---
*cr tool · 仅供参考*
"""


def save(report: ReviewReport, output_dir: str, runtime_ctx=None) -> str:
    import dataclasses
    import json
    d = Path(output_dir)
    d.mkdir(parents=True, exist_ok=True)
    safe_ts = report.timestamp.replace(":", "").replace(" ", "_")
    path = d / f"{safe_ts}_report.md"
    path.write_text(render(report), encoding="utf-8")

    # latest.json — fix_candidates（luna fix 使用）
    latest = d / "latest.json"
    latest.write_text(
        json.dumps(
            {"fix_candidates": [dataclasses.asdict(fc) for fc in report.fix_candidates]},
            ensure_ascii=False, indent=2,
        ),
        encoding="utf-8",
    )

    # {timestamp}_report.json — 结构化历史 sidecar（失败不影响主报告）
    try:
        all_items = (
            list(report.blast_radius_items)
            + list(report.code_quality_items)
            + list(report.backend_review_items)
        )
        from terminal_renderer import build_verdict
        verdict_label, _ = build_verdict(report)
        sidecar: dict = {
            "timestamp": report.timestamp,
            "commit": getattr(runtime_ctx, "commit_hash", "") if runtime_ctx else "",
            "verdict": verdict_label,
            "high":   sum(1 for i in all_items if i.risk == "high"),
            "medium": sum(1 for i in all_items if i.risk == "medium"),
            "low":    sum(1 for i in all_items if i.risk == "low"),
            "elapsed": getattr(runtime_ctx, "elapsed_seconds", 0.0) if runtime_ctx else 0.0,
            "items": [
                {"file": i.file, "line": i.line, "risk": i.risk,
                 "symbol": getattr(i, "symbol", ""),
                 "reason": getattr(i, "reason", getattr(i, "description", ""))}
                for i in all_items
            ],
            "fix_candidates": [dataclasses.asdict(fc) for fc in report.fix_candidates],
        }
        sidecar_path = d / f"{safe_ts}_report.json"
        sidecar_path.write_text(json.dumps(sidecar, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass  # sidecar 失败不影响 Markdown 报告和 latest.json

    return str(path)
