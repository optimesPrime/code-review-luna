from __future__ import annotations
import sys
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, List

if TYPE_CHECKING:
    from reporter import ReviewReport
    from runtime_context import RuntimeContext

try:
    from rich.console import Console
    from rich.panel import Panel
    from rich.text import Text
    from rich.table import Table
    from rich.tree import Tree
    RICH_AVAILABLE = True
except ImportError:
    RICH_AVAILABLE = False


# ── Verdict ──────────────────────────────────────────────────────────────────

_BLOCK_KEYWORDS = {"header", "request", "auth", "permission", "token", "cookie", "userId"}

def build_verdict(report: "ReviewReport") -> tuple[str, str]:
    """Return (label, style) for the verdict.

    Returns one of:
    - ("阻塞提交", "bold red")
    - ("建议修复后提交", "bold yellow")
    - ("可提交但建议关注", "yellow")
    - ("可提交", "bold green")
    """
    all_items = list(report.blast_radius_items) + list(report.backend_review_items)
    high_items = [i for i in all_items if i.risk == "high"]
    medium_items = [i for i in all_items if i.risk == "medium"]

    if high_items:
        # Escalate to 阻塞 if any high item: not needs_human_review AND hits critical keywords
        blocking = [
            i for i in high_items
            if not getattr(i, "needs_human_review", False) and _hits_block_keywords(i)
        ]
        if blocking:
            return "阻塞提交", "bold red"
        return "建议修复后提交", "bold yellow"

    if medium_items:
        return "可提交但建议关注", "yellow"

    return "可提交", "bold green"


def _hits_block_keywords(item) -> bool:
    text = f"{getattr(item, 'reason', '')} {getattr(item, 'evidence', '')}".lower()
    return any(kw in text for kw in _BLOCK_KEYWORDS)


# ── Risk counts ──────────────────────────────────────────────────────────────

def _count_risks(report: "ReviewReport") -> tuple[int, int, int]:
    """Return (high, medium, low) counts across all item types."""
    all_items = (
        list(report.blast_radius_items)
        + list(report.code_quality_items)
        + list(report.backend_review_items)
    )
    high = sum(1 for i in all_items if i.risk == "high")
    medium = sum(1 for i in all_items if i.risk == "medium")
    low = sum(1 for i in all_items if i.risk == "low")
    return high, medium, low


# ── Checkpoint matrix (Task 4) ────────────────────────────────────────────────

CHECKPOINTS = [
    ("请求上下文", ["header", "request", "context", "userid", "token", "cookie"]),
    ("状态同步",   ["store", "state", "sync", "init", "初始化", "持久化"]),
    ("页面跳转",   ["router", "redirect", "navigate", "跳转", "回首页"]),
    ("异常处理",   ["loading", "error", "catch", "exception", "失败", "恢复"]),
    ("权限/登录态", ["auth", "permission", "login", "logout", "权限", "登录"]),
    ("测试覆盖",   ["test", "spec", "assert", "断言", "覆盖"]),
    ("类型/空值",  ["null", "undefined", "type", "类型", "空值"]),
    ("样式/布局",  ["css", "style", "layout", "class", "样式"]),
    ("性能/重复请求", ["debounce", "throttle", "duplicate", "重复", "防抖"]),
]

_RISK_ORDER = {"high": 3, "medium": 2, "low": 1}


@dataclass
class CheckpointResult:
    name: str
    status: str    # "high" | "medium" | "low" | "ok"
    reason: str    # main risk description or "未发现明显风险"
    evidence: str  # file:line or "-"
    fix_mode: str  # "manual" | "assist" | "auto" | "-"


def _item_text(item) -> str:
    """Return searchable text for an item (reason + description/evidence)."""
    parts = [
        getattr(item, "reason", ""),
        getattr(item, "description", ""),
        getattr(item, "evidence", ""),
    ]
    return " ".join(p for p in parts if p).lower()


def _derive_fix_mode(item) -> str:
    """Derive fix_mode from item attributes using FixCandidate rules."""
    needs_human = getattr(item, "needs_human_review", False)
    issue_type = getattr(item, "issue_type", None)
    reason = getattr(item, "reason", "").lower()

    if needs_human:
        return "manual"

    if issue_type == "missing_error_handling":
        # auto only when no auth keywords in reason
        auth_keywords = {"auth", "permission", "login", "logout", "权限", "登录"}
        if not any(kw in reason for kw in auth_keywords):
            return "auto"
        return "manual"

    if issue_type in ("redundant", "dead_code"):
        return "auto"

    # blast item with needs_human_review=False (already covered above for True)
    if hasattr(item, "needs_human_review"):
        return "assist"

    return "manual"


def build_checkpoints(report: "ReviewReport") -> List[CheckpointResult]:
    """Build the checkpoint hit matrix from report items."""
    all_items = (
        list(report.blast_radius_items)
        + list(report.code_quality_items)
        + list(report.backend_review_items)
    )

    results: List[CheckpointResult] = []
    for name, keywords in CHECKPOINTS:
        matching = [
            item for item in all_items
            if any(kw in _item_text(item) for kw in keywords)
        ]

        if not matching:
            results.append(CheckpointResult(
                name=name,
                status="ok",
                reason="未发现明显风险",
                evidence="-",
                fix_mode="-",
            ))
            continue

        # Pick highest-risk representative item
        best = max(matching, key=lambda i: _RISK_ORDER.get(getattr(i, "risk", "low"), 0))

        raw_reason = getattr(best, "reason", None) or getattr(best, "description", "")
        truncated_reason = raw_reason[:60]

        results.append(CheckpointResult(
            name=name,
            status=best.risk,
            reason=truncated_reason,
            evidence=f"{best.file}:{best.line}",
            fix_mode=_derive_fix_mode(best),
        ))

    return results


def build_business_tree(report: "ReviewReport") -> object:
    """Stub — implemented in Task 5."""
    return None


def build_fix_queue(report: "ReviewReport") -> list:
    """Stub — implemented in Task 6."""
    return []


# ── Main render entry ─────────────────────────────────────────────────────────

def render_review(
    report: "ReviewReport",
    runtime: "RuntimeContext",
    fmt: str = "markdown",
    quiet: bool = False,
) -> None:
    """Render the review to stderr using Rich. No-op when fmt=='json'."""
    if fmt == "json":
        return

    if not RICH_AVAILABLE:
        _render_plain(report, runtime, quiet)
        return

    console = Console(stderr=True)
    _render_rich(console, report, runtime, quiet)


def _render_plain(report, runtime, quiet: bool) -> None:
    """Fallback plain-text render when Rich is not installed."""
    verdict_label, _ = build_verdict(report)
    high, medium, low = _count_risks(report)
    print(f"\n🌙 Luna Review", file=sys.stderr)
    print(f"Verdict: {verdict_label}", file=sys.stderr)
    if not quiet:
        print(f"项目: {runtime.project_name}  类型: {runtime.project_type}  范围: {runtime.diff_scope}", file=sys.stderr)
        print(f"改动: {runtime.changed_files} files / {runtime.changed_lines} lines  耗时: {runtime.elapsed_seconds}s", file=sys.stderr)
        print(f"风险: high={high}  medium={medium}  low={low}", file=sys.stderr)
        if runtime.report_path:
            print(f"报告: {runtime.report_path}", file=sys.stderr)


def _render_rich(console: "Console", report, runtime, quiet: bool) -> None:
    """Full Rich render."""
    verdict_label, verdict_style = build_verdict(report)
    high, medium, low = _count_risks(report)

    # Header
    console.print()
    console.print("[bold cyan]🌙 Luna Review[/bold cyan]")
    console.print()

    # Verdict panel
    verdict_icon = {
        "阻塞提交": "🚫",
        "建议修复后提交": "⚠️",
        "可提交但建议关注": "💡",
        "可提交": "✅",
    }.get(verdict_label, "")
    console.print(Panel(
        Text(f"{verdict_icon}  {verdict_label}", style=verdict_style, justify="center"),
        title="Verdict",
        border_style=verdict_style,
        padding=(0, 2),
    ))
    console.print()

    # Summary row
    backend_label = "skipped" if runtime.backend_review_status == "skipped" else runtime.backend_review_status
    summary = Table.grid(padding=(0, 2))
    summary.add_column(style="dim")
    summary.add_column()
    summary.add_row("项目:", runtime.project_name or "—")
    summary.add_row("类型:", runtime.project_type)
    summary.add_row("范围:", runtime.diff_scope)
    summary.add_row("后端审查:", backend_label)
    summary.add_row("改动:", f"{runtime.changed_files} files / {runtime.changed_lines} lines")
    summary.add_row("耗时:", f"{runtime.elapsed_seconds}s")
    if not quiet:
        summary.add_row("高风险:", f"[red]{high}[/red]" if high else str(high))
        summary.add_row("中风险:", f"[yellow]{medium}[/yellow]" if medium else str(medium))
        summary.add_row("低风险:", str(low))
    console.print(summary)
    console.print()

    if not quiet:
        # Checkpoint matrix
        checkpoints = build_checkpoints(report)
        if checkpoints:
            console.print("[bold]🔍 审查点命中[/bold]")
            tbl = Table(show_header=True, header_style="bold", box=None, padding=(0, 1))
            tbl.add_column("审查点", style="bold", min_width=10)
            tbl.add_column("状态", min_width=6)
            tbl.add_column("风险说明", min_width=20, max_width=50)
            tbl.add_column("证据", min_width=12)
            tbl.add_column("修复方式", min_width=8)
            for cp in checkpoints:
                status_str, status_style = {
                    "high":   ("🚨 high",   "red"),
                    "medium": ("⚠️ medium", "yellow"),
                    "low":    ("💡 low",    "blue"),
                    "ok":     ("✅ ok",     "green"),
                }.get(cp.status, (cp.status, ""))
                tbl.add_row(
                    cp.name,
                    Text(status_str, style=status_style),
                    cp.reason,
                    cp.evidence,
                    cp.fix_mode,
                )
            console.print(tbl)
            console.print()
        # Business tree (Task 5 stub)
        # Fix queue (Task 6 stub)

    # Footer
    if runtime.report_path:
        console.print(f"[dim]报告已保存: {runtime.report_path}[/dim]")
    console.print()
