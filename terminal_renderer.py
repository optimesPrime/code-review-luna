from __future__ import annotations
import sys
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, List

if TYPE_CHECKING:
    from reporter import ReviewReport
    from runtime_context import RuntimeContext

try:
    from rich.console import Console, Group
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

        results.append(CheckpointResult(
            name=name,
            status=best.risk,
            reason=raw_reason,
            evidence=f"{best.file}:{best.line}",
            fix_mode=_derive_fix_mode(best),
        ))

    # ── 数据库迁移专项 ────────────────────────────────────────────────────────
    migration_items = list(getattr(report, "migration_items", []))
    if not migration_items:
        results.append(CheckpointResult(
            name="数据库迁移", status="ok",
            reason="无迁移文件变更", evidence="-", fix_mode="-",
        ))
    else:
        best = max(migration_items, key=lambda i: _RISK_ORDER.get(getattr(i, "risk", "low"), 0))
        results.append(CheckpointResult(
            name="数据库迁移",
            status=best.risk,
            reason=best.reason,
            evidence=f"{best.file}:{best.line}",
            fix_mode="manual",
        ))

    # ── API 契约专项 ──────────────────────────────────────────────────────────
    api_items = list(getattr(report, "api_change_items", []))
    high_api = [i for i in api_items if i.risk == "high"]
    if not api_items:
        results.append(CheckpointResult(
            name="API 契约", status="ok",
            reason="无 API Schema 变更", evidence="-", fix_mode="-",
        ))
    elif not high_api:
        results.append(CheckpointResult(
            name="API 契约", status="low",
            reason=f"{len(api_items)} 处 API 变更（均为低风险）",
            evidence=api_items[0].file, fix_mode="-",
        ))
    else:
        best_api = max(high_api, key=lambda i: _RISK_ORDER.get(i.risk, 0))
        results.append(CheckpointResult(
            name="API 契约",
            status=best_api.risk,
            reason=best_api.reason,
            evidence=f"{best_api.file}:{best_api.line}",
            fix_mode="manual",
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
    file: str = ""
    line: int = 0
    evidence: str = ""
    suggestion: str = ""


@dataclass
class ChainNode:
    file: str
    line: int
    reason: str
    risk: str = "low"


@dataclass
class ImpactBlock:
    symbol_name: str
    risk: str
    chains: list  # list[list[ChainNode]]


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


def _group_impact_paths(report: "ReviewReport") -> list:
    """Group impact_paths by source symbol; annotate nodes from blast_radius_items."""
    valid_paths = [
        p for p in report.impact_paths
        if isinstance(p.get("path"), list) and len(p["path"]) >= 2
    ]
    if not valid_paths:
        return []

    _rv = {"high": 0, "medium": 1, "low": 2}

    blast_by_file: dict = {}
    for item in report.blast_radius_items:
        blast_by_file.setdefault(item.file, []).append(item)

    symbol_names: dict = {}
    for s in report.changed_symbols:
        f = s.get("file", "")
        symbol_names[f] = s.get("name", "") or f.split("/")[-1]

    from collections import defaultdict
    by_source: dict = defaultdict(list)
    for p in valid_paths:
        by_source[str(p["path"][0])].append(p)

    blocks = []
    for src, paths in by_source.items():
        symbol_name = symbol_names.get(src, src.split("/")[-1])
        block_risk = min(
            (p.get("risk", "low") for p in paths),
            key=lambda r: _rv.get(r, 2),
        )
        chains = []
        for p in paths:
            path_reason = str(p.get("reason", ""))
            chain = []
            path_nodes = p["path"]
            for i, node_file in enumerate(path_nodes[1:], 1):
                node_str = str(node_file)
                items = blast_by_file.get(node_str, [])
                if items:
                    best = min(items, key=lambda it: _rv.get(it.risk, 2))
                    chain.append(ChainNode(
                        file=node_str,
                        line=best.line,
                        reason=getattr(best, "reason", "") or "",
                        risk=best.risk,
                    ))
                else:
                    is_leaf = (i == len(path_nodes) - 1)
                    chain.append(ChainNode(
                        file=node_str,
                        line=0,
                        reason=path_reason if is_leaf else "",
                        risk=p.get("risk", "low"),
                    ))
            if chain:
                chains.append(chain)

        if chains:
            blocks.append(ImpactBlock(
                symbol_name=symbol_name,
                risk=block_risk,
                chains=chains,
            ))

    return blocks


def build_blast_chain(report: "ReviewReport", max_chains: int = 3, max_nodes: int = 5) -> list:
    """Return simplified path chain strings: 'a.ts → b.ts → c.ts'."""
    if report.impact_paths:
        chains = []
        for path_dict in report.impact_paths[:max_chains]:
            path = path_dict.get("path", [])
            if not isinstance(path, list) or not path:
                continue
            nodes = [str(p).split("/")[-1] for p in path[:max_nodes]]
            suffix = " → ..." if len(path) > max_nodes else ""
            chains.append(" → ".join(nodes) + suffix)
        if chains:
            return chains
    files = list(dict.fromkeys(i.file.split("/")[-1] for i in report.blast_radius_items))
    if files:
        return ["  ·  ".join(files[:max_nodes])]
    return []


def _cmd_for_item(item, fix_candidates: list) -> str | None:
    """Return the command hint for an item from the fix queue, or None."""
    for fc in fix_candidates:
        if fc.file == item.file and fc.line == item.line:
            return fc.command_hint
    return None


def _cmd_for_checkpoint(cp: "CheckpointResult", fix_candidates: list) -> str | None:
    """Return 'luna detail N' for the fix candidate matching this checkpoint's evidence."""
    if not cp.evidence or cp.evidence == "-" or ":" not in cp.evidence:
        return None
    parts = cp.evidence.rsplit(":", 1)
    if len(parts) != 2:
        return None
    try:
        file_part, line_part = parts[0], int(parts[1])
    except ValueError:
        return None
    for fc in fix_candidates:
        if fc.file == file_part and fc.line == line_part:
            return f"luna detail {fc.id}"
    return None


def _render_blast_section(console: "Console", report: "ReviewReport") -> None:
    """Render each changed symbol as an independent impact chain tree."""
    impact_paths = [
        p for p in report.impact_paths
        if isinstance(p.get("path"), list) and len(p["path"]) >= 2
    ]
    blast_items = list(report.blast_radius_items)

    if not impact_paths and not blast_items:
        return

    _rv         = {"high": 0, "medium": 1, "low": 2}
    _RISK_STYLE = {"high": "bold red", "medium": "bold yellow", "low": "cyan"}
    _RISK_ICON  = {"high": "🚨", "medium": "⚠️",  "low": "💡"}

    if impact_paths:
        blocks = _group_impact_paths(report)
        count  = sum(len(b.chains) for b in blocks)
        console.print(Rule(f"💥  影响链路  {count} 条", style="dim"))
        console.print()

        for block in blocks:
            b_icon  = _RISK_ICON.get(block.risk, "")
            b_style = _RISK_STYLE.get(block.risk, "dim")
            tree    = Tree(f"{b_icon}  [{b_style}]{block.symbol_name}[/{b_style}]")

            for chain in block.chains:
                current = tree
                for node in chain:
                    n_icon  = _RISK_ICON.get(node.risk, "")
                    n_style = _RISK_STYLE.get(node.risk, "dim")
                    fname   = node.file.split("/")[-1]
                    loc     = f":{node.line}" if node.line else ""
                    reason  = f"   [dim]{node.reason[:70]}[/dim]" if node.reason else ""
                    current = current.add(
                        f"{n_icon}  [{n_style}]{fname}{loc}[/{n_style}]{reason}"
                    )

            console.print(Padding(tree, (0, 2)))
        console.print()

    else:
        # Fallback: blast_radius_items → single tree under changed symbol
        changed    = list(report.changed_symbols)
        root_label = (
            (changed[0].get("name", "") or changed[0].get("file", "改动").split("/")[-1])
            if changed else "改动"
        )
        top_risk  = min((i.risk for i in blast_items), key=lambda r: _rv.get(r, 2), default="low")
        r_icon    = _RISK_ICON.get(top_risk, "")
        r_style   = _RISK_STYLE.get(top_risk, "dim")

        console.print(Rule("💥  影响链路", style="dim"))
        console.print()

        tree = Tree(f"{r_icon}  [{r_style}]{root_label}[/{r_style}]")
        for item in sorted(blast_items[:8], key=lambda i: _rv.get(i.risk, 3)):
            i_icon   = _RISK_ICON.get(item.risk, "")
            i_style  = _RISK_STYLE.get(item.risk, "dim")
            fname    = item.file.split("/")[-1]
            reason   = (getattr(item, "reason", "") or "")[:70]
            tree.add(
                f"{i_icon}  [{i_style}]{fname}:{item.line}[/{i_style}]"
                f"   [dim]{reason}[/dim]"
            )

        console.print(Padding(tree, (0, 2)))
        console.print()


def _render_item_card(console: "Console", item, fix_candidates: list, icon: str, style: str) -> None:
    """Render a 3-4 line expanded card for a high/medium risk item with inline command."""
    reason     = getattr(item, "reason", None) or getattr(item, "description", "")
    suggestion = getattr(item, "suggestion", "") or ""
    cmd        = _cmd_for_item(item, fix_candidates)

    header = Text()
    header.append(f"  {icon}  ", style=style)
    header.append(f"{item.file}:{item.line}", style="bold")
    header.append("  —  ")
    header.append(reason[:80])
    console.print(header)

    if suggestion:
        console.print(Padding(Text(suggestion[:120], style="dim"), (0, 6)))

    if cmd:
        auto_label = "  🤖 自动修复" if "fix" in cmd and "--preview" not in cmd else ""
        console.print(Padding(Text(f"$ {cmd}{auto_label}", style="bold green"), (0, 6)))

    console.print()


def _render_item_inline(console: "Console", item, fix_candidates: list) -> None:
    """Render a single compact line for a low risk item."""
    reason = getattr(item, "reason", None) or getattr(item, "description", "")
    cmd    = _cmd_for_item(item, fix_candidates)
    line   = Text()
    line.append("  💡  ", style="dim blue")
    line.append(f"{item.file}:{item.line}", style="bold")
    line.append("  —  ")
    line.append(reason[:60])
    if cmd:
        line.append(f"    $ {cmd}", style="bold green")
    console.print(line)


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
        title = getattr(item, "reason", None) or getattr(item, "description", "")
        reason = f"{item.file}:{item.line}"
        cmd = {"auto": f"luna fix {counter}", "assist": f"luna fix {counter} --preview"}.get(mode, f"luna detail {counter}")

        candidates.append(FixCandidate(
            id=counter,
            mode=mode,
            title=title,
            reason=reason,
            command_hint=cmd,
            impact=impact,
            file=item.file,
            line=item.line,
            evidence=getattr(item, "evidence", ""),
            suggestion=getattr(item, "suggestion", "") or "",
        ))
        counter += 1

    return candidates


def render_token_savings_panel(savings_per_phase: dict) -> str | None:
    """Return a plain-text savings box, or None when there is nothing to show.

    savings_per_phase: {"blast": {baseline, used, saved, saved_percent}, ...}
    """
    if not savings_per_phase:
        return None

    total_baseline = 0
    total_used = 0
    for phase_savings in savings_per_phase.values():
        b = phase_savings.get("baseline", 0)
        u = phase_savings.get("used", 0)
        if b > 0:
            total_baseline += b
            total_used += u

    if total_baseline <= 0:
        return None

    total_saved = max(0, total_baseline - total_used)
    total_pct = round(total_saved * 100 / total_baseline)

    inner_lines = [
        f"如果直接传 diff：  {total_baseline:>10,} tokens",
        f"实际使用：         {total_used:>10,} tokens",
        f"节省：             {total_saved:>10,} tokens ({total_pct}%)",
    ]
    inner_w = max(len(s) for s in inner_lines) + 2
    title = " Token 使用情况 "
    dash = max(4, inner_w - len(title))
    top = "┌" + "─" * (dash // 2) + title + "─" * (dash - dash // 2) + "┐"
    bot = "└" + "─" * inner_w + "┘"
    rows = [top] + [f"│ {s}{' ' * (inner_w - 2 - len(s))} │" for s in inner_lines] + [bot]
    return "\n".join(rows)


def render_diff_preview(patch: str) -> None:
    """Print a syntax-highlighted unified diff to stderr using Rich."""
    if not RICH_AVAILABLE:
        import sys
        print(patch, file=sys.stderr)
        return
    from rich.console import Console
    from rich.syntax import Syntax
    Console(stderr=True).print(Syntax(patch, "diff", theme="monokai", word_wrap=True))


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
    """Full Rich render — v2: item cards with inline commands."""
    verdict_label, verdict_style = build_verdict(report)
    high, medium, low = _count_risks(report)

    # ── 标题 ─────────────────────────────────────────────────────────────────
    info_parts = [
        runtime.project_name or "—",
        runtime.project_type,
        f"{runtime.changed_files} files  {runtime.changed_lines} lines",
        f"{runtime.elapsed_seconds}s",
    ]
    title_text = "  ·  ".join(p for p in info_parts if p)
    console.print()
    console.print(Rule(f"[bold cyan]🌙  Luna Review[/bold cyan]  [dim]{title_text}[/dim]", style="cyan"))
    console.print()

    # ── Verdict + 风险数 ──────────────────────────────────────────────────────
    _verdict_icon = {
        "阻塞提交":        "🚫",
        "建议修复后提交":   "⚠️",
        "可提交但建议关注": "💡",
        "可提交":          "✅",
    }
    verdict_line = Text()
    verdict_line.append(f"{_verdict_icon.get(verdict_label, '')}  {verdict_label}", style=verdict_style)
    verdict_line.append("     ")
    verdict_line.append("🚨 ")
    verdict_line.append(str(high),   style="bold red"    if high   else "dim")
    verdict_line.append("   ⚠️  ")
    verdict_line.append(str(medium), style="bold yellow" if medium else "dim")
    verdict_line.append("   💡 ")
    verdict_line.append(str(low),    style="bold blue"   if low    else "dim")
    console.print(Padding(verdict_line, (0, 2)))
    console.print()

    if quiet:
        if runtime.report_path:
            console.print(Rule(f"[dim]报告: {runtime.report_path}[/dim]", style="dim"))
        return

    # build fix queue once — provides inline command hints throughout
    fix_candidates = build_fix_queue(report)

    # ── 必须修复 ──────────────────────────────────────────────────────────────
    all_items = list(report.blast_radius_items) + list(report.code_quality_items)
    high_items = [i for i in all_items if i.risk == "high"]
    if high_items:
        console.print(Rule("[bold]🔴  必须修复[/bold]", style="dim"))
        console.print()
        for item in high_items[:5]:
            _render_item_card(console, item, fix_candidates, icon="🚨", style="bold red")
        overflow = len(high_items) - 5
        if overflow > 0:
            console.print(Padding(
                Text(f"  + {overflow} 条高风险，运行 luna detail 查看完整报告", style="dim yellow"),
                (0, 4),
            ))
            console.print()

    # ── 建议修复 ──────────────────────────────────────────────────────────────
    medium_items = [i for i in all_items if i.risk == "medium"]
    low_items    = [i for i in all_items if i.risk == "low"]
    if medium_items or low_items:
        console.print(Rule("[bold]⚠️   建议修复[/bold]", style="dim"))
        console.print()
        for item in medium_items:
            _render_item_card(console, item, fix_candidates, icon="⚠️", style="bold yellow")
        for item in low_items:
            _render_item_inline(console, item, fix_candidates)
        if low_items:
            console.print()

    # ── 爆炸范围 ──────────────────────────────────────────────────────────────
    _render_blast_section(console, report)

    # ── 审查点命中 ────────────────────────────────────────────────────────────
    checkpoints = build_checkpoints(report)
    hit_cps = [cp for cp in checkpoints if cp.status != "ok"]
    if hit_cps:
        console.print(Rule("🔍  审查点命中", style="dim"))
        console.print()
        _cp_icon  = {"high": "🚨", "medium": "⚠️", "low": "💡"}
        _cp_style = {"high": "bold red", "medium": "bold yellow", "low": "dim blue"}
        for cp in hit_cps:
            icon  = _cp_icon.get(cp.status, "")
            style = _cp_style.get(cp.status, "")
            cmd   = _cmd_for_checkpoint(cp, fix_candidates)
            line  = Text()
            line.append(f"  {icon} ", style=style)
            line.append(f"{cp.name:<12}", style=style)
            line.append(f"  {cp.evidence:<28}", style="dim")
            if cmd:
                line.append(f"  $ {cmd}", style="bold green")
            console.print(line)
        console.print()

    # ── 反驳验证 ──────────────────────────────────────────────────────────────
    refuted = getattr(report, "adversarial_refuted", [])
    if refuted:
        console.print(Rule("🔬  反驳验证 — 已过滤误报", style="dim"))
        console.print()
        rf_tbl = Table(
            show_header=True, header_style="bold dim",
            box=rich_box.SIMPLE, padding=(0, 1), border_style="dim", expand=True,
        )
        rf_tbl.add_column("符号",             style="dim", min_width=12, no_wrap=True)
        rf_tbl.add_column("位置",             style="dim", min_width=16, no_wrap=True)
        rf_tbl.add_column("原因（已被反驳）",  min_width=20, ratio=3)
        rf_tbl.add_column("反驳理由",          min_width=20, ratio=3)
        for rf in refuted:
            item = rf.item
            sym  = getattr(item, "symbol", "") or "—"
            rf_tbl.add_row(
                Text(sym, style="dim"),
                Text(f"{item.file}:{item.line}", style="dim"),
                Text(getattr(item, "reason", ""), style="dim strike"),
                Text(rf.adv_reason, style="dim green"),
            )
        console.print(rf_tbl)
        console.print()

    # ── 页脚 ──────────────────────────────────────────────────────────────────
    if runtime.report_path:
        console.print(Rule(f"[dim]报告: {runtime.report_path}[/dim]", style="dim"))
    console.print()
