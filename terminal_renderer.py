from __future__ import annotations
import sys
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

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


# ── Stubs for Tasks 4-6 (will be replaced) ──────────────────────────────────

def build_checkpoints(report: "ReviewReport") -> object:
    """Stub — implemented in Task 4."""
    return None


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
        # Checkpoint matrix (Task 4 stub)
        # Business tree (Task 5 stub)
        # Fix queue (Task 6 stub)
        pass

    # Footer
    if runtime.report_path:
        console.print(f"[dim]报告已保存: {runtime.report_path}[/dim]")
    console.print()
