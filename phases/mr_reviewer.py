from __future__ import annotations
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from reporter import ReviewReport

_RISK_ORDER = {"high": 2, "medium": 1, "low": 0}
_RISK_EMOJI = {"high": "🚨", "medium": "⚠️", "low": "💡"}


def map_items_to_positions(items: list, diff_refs: dict) -> list[tuple]:
    """将 blast/quality items 映射到 GitLab diff position，跳过无法定位的条目。"""
    result = []
    for item in items:
        if not getattr(item, "file", None) or not getattr(item, "line", None):
            continue
        result.append((item, diff_refs))
    return result


def build_summary_comment(report: "ReviewReport", prefix: str = "🌙 Luna Review") -> str:
    all_items = list(report.blast_radius_items) + list(report.code_quality_items)
    high   = sum(1 for i in all_items if i.risk == "high")
    medium = sum(1 for i in all_items if i.risk == "medium")
    low    = sum(1 for i in all_items if i.risk == "low")

    if high:
        verdict = "🚫 阻塞提交"
    elif medium:
        verdict = "⚠️ 建议修复后提交"
    else:
        verdict = "✅ 可提交"

    lines = [
        f"## {prefix}",
        "",
        f"{verdict} · 🚨 {high} 高风险  ⚠️ {medium} 中风险  💡 {low} 低风险",
    ]

    top_items = sorted(
        [i for i in all_items if i.risk in ("high", "medium")],
        key=lambda i: -_RISK_ORDER.get(i.risk, 0),
    )[:10]

    if top_items:
        lines += ["", "### 主要风险"]
        for item in top_items:
            icon   = _RISK_EMOJI.get(item.risk, "")
            reason = getattr(item, "reason", None) or getattr(item, "description", "")
            lines.append(f"- {icon} `{item.file}:{item.line}` — {reason}")

    refuted = getattr(report, "adversarial_refuted", [])
    if refuted:
        lines += ["", f"*反驳验证已过滤 {len(refuted)} 条误报*"]

    return "\n".join(lines)


def build_inline_comment(item, prefix: str = "🌙 Luna Review") -> str:
    icon       = _RISK_EMOJI.get(item.risk, "")
    reason     = getattr(item, "reason", None) or getattr(item, "description", "")
    confidence = getattr(item, "confidence", "")
    suggestion = getattr(item, "suggestion", "")

    lines = [
        f"**{prefix}** {icon} **{item.risk} 风险**",
        "",
        reason,
    ]
    if confidence:
        lines.append(f"置信度: `{confidence}`")
    if suggestion:
        lines += ["", f"**建议：** {suggestion}"]
    return "\n".join(lines)
