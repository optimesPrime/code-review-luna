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
    from rich.rule import Rule
    from rich.columns import Columns
    from rich.align import Align
    from rich.padding import Padding
    from rich import box as rich_box
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


RISK_ICON = {"high": "🚨", "medium": "⚠️", "low": "💡", "ok": "✅"}


def build_business_tree(report: "ReviewReport"):
    """Build Rich Tree for business impact. Returns None if nothing to show."""
    if not RICH_AVAILABLE:
        return None

    blast_items = list(report.blast_radius_items)
    impact_paths = list(report.impact_paths)  # list of dicts
    changed_symbols = list(report.changed_symbols)  # list of dicts

    if not blast_items and not impact_paths:
        return None

    # Determine root label
    if changed_symbols:
        root_name = changed_symbols[0].get("name", "") or changed_symbols[0].get("symbol", "改动影响范围")
    else:
        root_name = "改动影响范围"
    root_name = root_name[:60]

    tree = Tree(f"[bold cyan]💥 {root_name}[/bold cyan]")

    if impact_paths:
        # Strategy 1: use impact_paths
        for path_dict in impact_paths[:10]:  # cap at 10 to avoid overwhelming output
            risk = path_dict.get("risk", "low")
            icon = RISK_ICON.get(risk, "")
            # Try to get a meaningful label
            path_val = path_dict.get("path", [])
            if isinstance(path_val, list) and path_val:
                label = " → ".join(str(p) for p in path_val[-2:])  # last 2 hops
            else:
                label = str(path_val)
            label = label[:60]
            evidence = path_dict.get("evidence", "")
            reason = str(path_dict.get("reason", ""))[:50]
            branch = tree.add(f"{icon} [{risk}] {label}")
            if reason:
                branch.add(f"[dim]{reason}[/dim]")
            if evidence:
                branch.add(f"[dim]证据: {str(evidence)[:50]}[/dim]")
    else:
        # Strategy 2: group blast items by checkpoint
        grouped: dict = {}
        for item in blast_items:
            item_text = f"{item.reason} {item.file}".lower()
            matched = False
            for cp_name, keywords in CHECKPOINTS:
                if any(kw in item_text for kw in keywords):
                    grouped.setdefault(cp_name, []).append(item)
                    matched = True
                    break  # assign to first matching checkpoint only
            if not matched:
                grouped.setdefault("业务逻辑", []).append(item)

        for cp_name, items in grouped.items():
            # Find highest risk in this group
            top_risk = max(items, key=lambda i: {"high": 2, "medium": 1, "low": 0}.get(i.risk, 0))
            icon = RISK_ICON.get(top_risk.risk, "")
            branch = tree.add(f"{icon} {cp_name}")
            for item in items[:3]:  # max 3 items per group
                leaf_text = f"{item.reason[:50]} ({item.file}:{item.line})"
                branch.add(f"[dim]{leaf_text}[/dim]")

    return tree


@dataclass
class FixCandidate:
    id: int
    mode: str       # "auto" | "assist" | "manual"
    title: str
    reason: str
    command_hint: str
    impact: str     # "阻塞" | "高价值" | "建议" | "延后"


def _classify_fix_candidate(item) -> tuple[str, str]:
    risk = item.risk
    needs_human = getattr(item, "needs_human_review", False)
    issue_type = getattr(item, "issue_type", "")
    text = (getattr(item, "reason", "") + " " + getattr(item, "description", "")).lower()
    _auth = {"auth", "permission", "login", "logout", "权限", "登录"}

    if needs_human:
        return "manual", ("阻塞" if risk == "high" else "高价值")
    if issue_type == "missing_error_handling" and not any(kw in text for kw in _auth):
        return "auto", "建议"
    if issue_type in ("redundant", "dead_code"):
        return "auto", "延后"
    if hasattr(item, "needs_human_review"):  # is a BlastRadiusItem
        return "assist", ("高价值" if risk == "high" else "建议")
    return "manual", "建议"


def build_fix_queue(report: "ReviewReport") -> list:
    """Build a prioritised list of FixCandidates from report items."""
    candidates = []
    counter = 1

    all_items = list(report.blast_radius_items) + list(report.code_quality_items)
    # Sort: high first, then medium, then low
    all_items.sort(key=lambda i: {"high": 0, "medium": 1, "low": 2}.get(i.risk, 3))

    for item in all_items:
        # Must have suggestion OR be high risk
        if not getattr(item, "suggestion", None) and item.risk != "high":
            continue

        mode, impact = _classify_fix_candidate(item)
        title = (getattr(item, "reason", None) or getattr(item, "description", ""))[:50]
        reason = f"{item.file}:{item.line}"
        cmd = {"auto": f"luna fix {counter} --apply", "assist": f"luna fix {counter} --preview"}.get(mode, f"luna detail {counter}")

        candidates.append(FixCandidate(
            id=counter,
            mode=mode,
            title=title,
            reason=reason,
            command_hint=cmd,
            impact=impact,
        ))
        counter += 1

    return candidates


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

    # ── 标题 ─────────────────────────────────────────────────────────────────
    console.print()
    console.print(Rule("[bold cyan]🌙  Luna Review[/bold cyan]", style="cyan"))
    console.print()

    # ── Verdict（居中，固定宽度，不铺满全屏）─────────────────────────────────
    verdict_icon = {
        "阻塞提交":        "🚫",
        "建议修复后提交":   "⚠️",
        "可提交但建议关注": "💡",
        "可提交":          "✅",
    }.get(verdict_label, "")
    verdict_panel = Panel(
        Text(f"{verdict_icon}  {verdict_label}", style=verdict_style, justify="center"),
        border_style=verdict_style,
        padding=(1, 6),
        width=52,
    )
    console.print(Align.center(verdict_panel))
    console.print()

    # ── 摘要：一行信息条 + 一行风险徽章 ──────────────────────────────────────
    backend_label = "skipped" if runtime.backend_review_status == "skipped" else runtime.backend_review_status
    sep = Text("  ·  ", style="dim")

    info_line = Text()
    info_line.append(runtime.project_name or "—", style="bold")
    info_line.append("  ·  ", style="dim")
    info_line.append(runtime.project_type, style="cyan")
    info_line.append("  ·  ", style="dim")
    info_line.append(runtime.diff_scope, style="dim")
    info_line.append("  ·  ", style="dim")
    info_line.append(f"{runtime.changed_files} files  {runtime.changed_lines} lines", style="dim")
    info_line.append("  ·  ", style="dim")
    info_line.append(f"{runtime.elapsed_seconds}s", style="dim")
    info_line.append("  ·  ", style="dim")
    info_line.append(f"后端: {backend_label}", style="dim")

    risk_line = Text()
    risk_line.append("  🚨 ", style="")
    risk_line.append(str(high), style="bold red" if high else "dim")
    risk_line.append("   ⚠️  ", style="")
    risk_line.append(str(medium), style="bold yellow" if medium else "dim")
    risk_line.append("   💡 ", style="")
    risk_line.append(str(low), style="bold blue" if low else "dim")

    console.print(Padding(info_line, (0, 2)))
    console.print(Padding(risk_line, (0, 2)))
    console.print()

    if quiet:
        if runtime.report_path:
            console.print(Rule(f"[dim]报告: {runtime.report_path}[/dim]", style="dim"))
        return

    # ── 审查点命中 ────────────────────────────────────────────────────────────
    checkpoints = build_checkpoints(report)
    console.print(Rule("🔍  审查点命中", style="dim"))
    console.print()
    tbl = Table(
        show_header=True,
        header_style="bold dim",
        box=rich_box.SIMPLE_HEAD,
        padding=(0, 1),
        show_edge=False,
    )
    tbl.add_column("审查点",  style="bold", min_width=10, no_wrap=True)
    tbl.add_column("状态",    min_width=10, no_wrap=True)
    tbl.add_column("风险说明", max_width=34, no_wrap=True, overflow="ellipsis")
    tbl.add_column("证据",    max_width=22, no_wrap=True, overflow="ellipsis", style="dim")
    tbl.add_column("修复",    min_width=7,  no_wrap=True)
    _status_map = {
        "high":   ("🚨 high",    "bold red"),
        "medium": ("⚠️  medium", "bold yellow"),
        "low":    ("💡 low",     "bold blue"),
        "ok":     ("✅ ok",      "dim green"),
    }
    for cp in checkpoints:
        status_str, status_style = _status_map.get(cp.status, (cp.status, ""))
        fix_style = {"manual": "red", "assist": "yellow", "auto": "green"}.get(cp.fix_mode, "dim")
        tbl.add_row(
            cp.name,
            Text(status_str, style=status_style),
            cp.reason,
            cp.evidence,
            Text(cp.fix_mode, style=fix_style),
        )
    console.print(tbl)
    console.print()

    # ── 业务爆炸图 ────────────────────────────────────────────────────────────
    console.print(Rule("💥  业务爆炸图", style="dim"))
    console.print()
    tree = build_business_tree(report)
    if tree is not None:
        console.print(Padding(tree, (0, 2)))
    else:
        console.print(Padding("[dim]未发现明确传播链路[/dim]", (0, 2)))
    console.print()

    # ── 修复队列 ──────────────────────────────────────────────────────────────
    fix_queue = build_fix_queue(report)
    if fix_queue:
        console.print(Rule("🛠  修复队列", style="dim"))
        console.print()
        fq_tbl = Table(
            show_header=True,
            header_style="bold dim",
            box=rich_box.SIMPLE_HEAD,
            padding=(0, 1),
            show_edge=False,
        )
        fq_tbl.add_column("#",   style="dim",  min_width=2,  justify="right", no_wrap=True)
        fq_tbl.add_column("模式", min_width=7,  no_wrap=True)
        fq_tbl.add_column("影响", min_width=6,  no_wrap=True)
        fq_tbl.add_column("说明", max_width=38, no_wrap=True, overflow="ellipsis")
        fq_tbl.add_column("命令", style="dim",  max_width=24, no_wrap=True, overflow="ellipsis")
        _mode_style  = {"auto": "green", "assist": "yellow", "manual": "red"}
        _impact_style = {"阻塞": "bold red", "高价值": "red", "建议": "yellow", "延后": "dim"}
        for fc in fix_queue:
            fq_tbl.add_row(
                str(fc.id),
                Text(fc.mode,   style=_mode_style.get(fc.mode, "")),
                Text(fc.impact, style=_impact_style.get(fc.impact, "")),
                fc.title,
                fc.command_hint,
            )
        console.print(fq_tbl)
        console.print()

    # ── 页脚 ──────────────────────────────────────────────────────────────────
    if runtime.report_path:
        console.print(Rule(f"[dim]报告: {runtime.report_path}[/dim]", style="dim"))
    console.print()
