# Output Redesign v2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> ⚠️ **约束：所有改动不擅自 commit，等布哥哥明确要求再提交**

**Goal:** 重写 `_render_rich`，实现「问题卡片 + 内联命令」布局：每条问题直接显示 `$ luna fix N` 或 `$ luna detail N`，删除独立修复队列表格和嵌套圆环爆炸地图，审查点只列命中项。

**Architecture:** 单文件改动（`terminal_renderer.py`）。新增 4 个辅助函数（`build_blast_chain`、`_cmd_for_item`、`_cmd_for_checkpoint`、`_render_item_card`），重写 `_render_rich`，删除 `build_explosion_map`。`build_fix_queue` / `FixCandidate` 保留（luna fix 命令依赖）。TDD：先写失败测试，再实现，每个 Task 最后全量回归。

**Tech Stack:** Python 3.11+、Rich（已有）、pytest（已有）

---

## 文件地图

| 操作 | 路径 |
|------|------|
| Modify | `terminal_renderer.py` |
| Modify | `tests/test_terminal_renderer.py` |

测试辅助函数（已有，直接复用）：
- `_make_report(blast, quality, backend)` → `ReviewReport`
- `_blast(risk, reason, needs_human_review)` → `BlastRadiusItem`
- `_quality(risk, issue_type)` → `CodeQualityItem`

---

## Task 1: `build_blast_chain` — 爆炸链路字符串

**文件：**
- Modify: `terminal_renderer.py`（在 `build_fix_queue` 之前插入）
- Modify: `tests/test_terminal_renderer.py`（末尾追加）

- [ ] **Step 1: 写失败测试**

在 `tests/test_terminal_renderer.py` 末尾追加：

```python
class TestBuildBlastChain:
    def test_from_impact_paths(self):
        from terminal_renderer import build_blast_chain
        r = _make_report()
        r.impact_paths = [{"path": ["src/a.ts", "src/b.ts", "src/c.ts"], "risk": "high"}]
        chains = build_blast_chain(r)
        assert len(chains) == 1
        assert "a.ts" in chains[0]
        assert "→" in chains[0]

    def test_max_3_chains(self):
        from terminal_renderer import build_blast_chain
        r = _make_report()
        r.impact_paths = [
            {"path": ["a.ts", "b.ts"], "risk": "high"},
            {"path": ["c.ts", "d.ts"], "risk": "medium"},
            {"path": ["e.ts", "f.ts"], "risk": "low"},
            {"path": ["g.ts", "h.ts"], "risk": "low"},
        ]
        assert len(build_blast_chain(r)) == 3

    def test_fallback_to_blast_files_when_no_impact_paths(self):
        from terminal_renderer import build_blast_chain
        from phases.blast_radius import BlastRadiusItem
        item_a = BlastRadiusItem(file="src/store.ts", line=1, symbol="x", risk="high", confidence="medium", reason="r")
        item_b = BlastRadiusItem(file="src/auth.ts",  line=2, symbol="y", risk="high", confidence="medium", reason="r")
        r = _make_report(blast=[item_a, item_b])
        chains = build_blast_chain(r)
        assert len(chains) == 1
        assert "store.ts" in chains[0]
        assert "auth.ts" in chains[0]

    def test_empty_when_no_data(self):
        from terminal_renderer import build_blast_chain
        r = _make_report()
        assert build_blast_chain(r) == []

    def test_truncates_long_path(self):
        from terminal_renderer import build_blast_chain
        r = _make_report()
        r.impact_paths = [{"path": [f"file{i}.ts" for i in range(10)], "risk": "high"}]
        chains = build_blast_chain(r)
        assert "..." in chains[0]
```

- [ ] **Step 2: 确认测试失败**

```bash
cd /Users/wangyinlong/luna
python3 -m pytest tests/test_terminal_renderer.py::TestBuildBlastChain -v 2>&1 | head -15
```

期望：`ImportError: cannot import name 'build_blast_chain'`

- [ ] **Step 3: 实现 `build_blast_chain`**

在 `terminal_renderer.py` 的 `build_fix_queue` 函数之前添加：

```python
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
```

- [ ] **Step 4: 确认测试通过**

```bash
python3 -m pytest tests/test_terminal_renderer.py::TestBuildBlastChain -v
```

期望：5 个全绿

- [ ] **Step 5: 全量回归**

```bash
python3 -m pytest -q --tb=no 2>&1 | tail -3
```

期望：全绿

---

## Task 2: `_cmd_for_item` 和 `_cmd_for_checkpoint` — 命令查找

**文件：**
- Modify: `terminal_renderer.py`（在 `build_blast_chain` 之后插入）
- Modify: `tests/test_terminal_renderer.py`（末尾追加）

- [ ] **Step 1: 写失败测试**

追加到 `tests/test_terminal_renderer.py`：

```python
class TestCmdForItem:
    def _make_fc(self, id, file, line, mode, hint):
        from terminal_renderer import FixCandidate
        return FixCandidate(id=id, mode=mode, title="t", reason="r",
                            command_hint=hint, impact="高价值",
                            file=file, line=line)

    def test_returns_command_hint_when_matched(self):
        from terminal_renderer import _cmd_for_item
        fc = self._make_fc(1, "src/auth.ts", 18, "assist", "luna fix 1 --preview")
        item = _blast("high", "r")
        item.file = "src/auth.ts"
        item.line = 18
        assert _cmd_for_item(item, [fc]) == "luna fix 1 --preview"

    def test_returns_none_when_no_match(self):
        from terminal_renderer import _cmd_for_item
        fc = self._make_fc(1, "src/auth.ts", 18, "assist", "luna fix 1 --preview")
        item = _blast("high", "r")
        item.file = "src/other.ts"
        item.line = 99
        assert _cmd_for_item(item, [fc]) is None

    def test_returns_none_when_empty_candidates(self):
        from terminal_renderer import _cmd_for_item
        item = _blast("high", "r")
        assert _cmd_for_item(item, []) is None


class TestCmdForCheckpoint:
    def test_returns_luna_detail_when_matched(self):
        from terminal_renderer import _cmd_for_checkpoint, CheckpointResult, FixCandidate
        cp = CheckpointResult(name="权限/登录态", status="high",
                              reason="auth missing", evidence="src/auth.ts:18", fix_mode="assist")
        fc = FixCandidate(id=3, mode="assist", title="t", reason="r",
                          command_hint="luna fix 3 --preview", impact="高价值",
                          file="src/auth.ts", line=18)
        assert _cmd_for_checkpoint(cp, [fc]) == "luna detail 3"

    def test_returns_none_when_no_evidence(self):
        from terminal_renderer import _cmd_for_checkpoint, CheckpointResult
        cp = CheckpointResult(name="测试覆盖", status="ok",
                              reason="ok", evidence="-", fix_mode="-")
        assert _cmd_for_checkpoint(cp, []) is None

    def test_returns_none_when_no_match(self):
        from terminal_renderer import _cmd_for_checkpoint, CheckpointResult
        cp = CheckpointResult(name="权限/登录态", status="high",
                              reason="auth missing", evidence="src/auth.ts:18", fix_mode="assist")
        assert _cmd_for_checkpoint(cp, []) is None
```

- [ ] **Step 2: 确认测试失败**

```bash
python3 -m pytest tests/test_terminal_renderer.py::TestCmdForItem tests/test_terminal_renderer.py::TestCmdForCheckpoint -v 2>&1 | head -15
```

期望：`ImportError: cannot import name '_cmd_for_item'`

- [ ] **Step 3: 实现两个函数**

在 `terminal_renderer.py` 的 `build_blast_chain` 之后添加：

```python
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
```

- [ ] **Step 4: 确认测试通过**

```bash
python3 -m pytest tests/test_terminal_renderer.py::TestCmdForItem tests/test_terminal_renderer.py::TestCmdForCheckpoint -v
```

期望：6 个全绿

- [ ] **Step 5: 全量回归**

```bash
python3 -m pytest -q --tb=no 2>&1 | tail -3
```

期望：全绿

---

## Task 3: `_render_item_card` 和 `_render_item_inline` — 渲染辅助

**文件：**
- Modify: `terminal_renderer.py`（在 `_cmd_for_checkpoint` 之后插入）
- Modify: `tests/test_terminal_renderer.py`（末尾追加）

- [ ] **Step 1: 写失败测试**

追加到 `tests/test_terminal_renderer.py`：

```python
class TestRenderItemCard:
    def _render(self, item, fix_candidates=(), icon="🚨", style="bold red"):
        from terminal_renderer import _render_item_card, RICH_AVAILABLE
        import io
        if not RICH_AVAILABLE:
            pytest.skip("Rich not installed")
        from rich.console import Console
        buf = io.StringIO()
        console = Console(file=buf, no_color=True, highlight=False)
        _render_item_card(console, item, list(fix_candidates), icon=icon, style=style)
        return buf.getvalue()

    def test_shows_file_and_reason(self):
        item = _blast("high", "权限校验缺失")
        item.file = "src/auth.ts"
        item.line = 18
        output = self._render(item)
        assert "src/auth.ts:18" in output
        assert "权限校验缺失" in output

    def test_shows_suggestion_when_present(self):
        from phases.blast_radius import BlastRadiusItem
        item = BlastRadiusItem(file="f.ts", line=1, symbol="x", risk="high",
                               confidence="medium", reason="r", suggestion="加装饰器")
        output = self._render(item)
        assert "加装饰器" in output

    def test_shows_command_when_matched(self):
        from terminal_renderer import FixCandidate
        item = _blast("high", "r")
        item.file = "src/auth.ts"
        item.line = 18
        fc = FixCandidate(id=1, mode="assist", title="t", reason="r",
                          command_hint="luna fix 1 --preview", impact="高价值",
                          file="src/auth.ts", line=18)
        output = self._render(item, fix_candidates=[fc])
        assert "luna fix 1 --preview" in output

    def test_no_command_when_no_match(self):
        item = _blast("high", "r")
        output = self._render(item, fix_candidates=[])
        assert "luna fix" not in output
        assert "luna detail" not in output


class TestRenderItemInline:
    def _render(self, item, fix_candidates=()):
        from terminal_renderer import _render_item_inline, RICH_AVAILABLE
        import io
        if not RICH_AVAILABLE:
            pytest.skip("Rich not installed")
        from rich.console import Console
        buf = io.StringIO()
        console = Console(file=buf, no_color=True, highlight=False)
        _render_item_inline(console, item, list(fix_candidates))
        return buf.getvalue()

    def test_shows_file_and_truncated_reason(self):
        item = _blast("low", "some low risk thing")
        item.file = "src/router.ts"
        item.line = 7
        output = self._render(item)
        assert "src/router.ts:7" in output
        assert "some low risk thing" in output

    def test_single_line(self):
        item = _blast("low", "r")
        output = self._render(item)
        assert output.count("\n") <= 2  # one content line + trailing newline
```

- [ ] **Step 2: 确认测试失败**

```bash
python3 -m pytest tests/test_terminal_renderer.py::TestRenderItemCard tests/test_terminal_renderer.py::TestRenderItemInline -v 2>&1 | head -15
```

期望：`ImportError: cannot import name '_render_item_card'`

- [ ] **Step 3: 实现两个渲染辅助函数**

在 `terminal_renderer.py` 的 `_cmd_for_checkpoint` 之后添加：

```python
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
```

- [ ] **Step 4: 确认测试通过**

```bash
python3 -m pytest tests/test_terminal_renderer.py::TestRenderItemCard tests/test_terminal_renderer.py::TestRenderItemInline -v
```

期望：6 个全绿

- [ ] **Step 5: 全量回归**

```bash
python3 -m pytest -q --tb=no 2>&1 | tail -3
```

期望：全绿

---

## Task 4: 重写 `_render_rich` + 删除 `build_explosion_map`

**文件：**
- Modify: `terminal_renderer.py`（重写 `_render_rich`，删除 `build_explosion_map`）
- Modify: `tests/test_terminal_renderer.py`（新增集成测试，删除引用 `build_explosion_map` 的旧测试）

- [ ] **Step 1: 写失败测试（集成）**

追加到 `tests/test_terminal_renderer.py`：

```python
class TestNewRenderRich:
    def _make_runtime(self):
        from runtime_context import RuntimeContext
        return RuntimeContext()

    def _render(self, report, quiet=False):
        from terminal_renderer import _render_rich, RICH_AVAILABLE
        import io
        if not RICH_AVAILABLE:
            pytest.skip("Rich not installed")
        from rich.console import Console
        buf = io.StringIO()
        console = Console(file=buf, no_color=True, highlight=False)
        _render_rich(console, report, self._make_runtime(), quiet=quiet)
        return buf.getvalue()

    def test_high_risk_shows_in_must_fix_section(self):
        r = _make_report(blast=[_blast("high", "权限缺失")])
        r.blast_radius_items[0].file = "src/auth.ts"
        output = self._render(r)
        assert "必须修复" in output
        assert "src/auth.ts" in output

    def test_command_inline_with_item(self):
        from phases.blast_radius import BlastRadiusItem
        item = BlastRadiusItem(file="src/auth.ts", line=18, symbol="x",
                               risk="high", confidence="medium",
                               reason="权限缺失", suggestion="加装饰器",
                               needs_human_review=False)
        r = _make_report(blast=[item])
        output = self._render(r)
        assert "luna fix" in output or "luna detail" in output

    def test_no_fix_queue_table(self):
        r = _make_report(blast=[_blast("high", "r")])
        output = self._render(r)
        assert "修复队列" not in output

    def test_no_verdict_panel(self):
        r = _make_report()
        output = self._render(r)
        assert "╭" not in output  # Rich Panel 边框字符

    def test_no_token_savings(self):
        r = _make_report()
        r.token_savings = {"blast": {"baseline": 1000, "used": 500, "saved": 500, "saved_percent": 50}}
        output = self._render(r)
        assert "Token" not in output

    def test_checkpoint_section_absent_when_all_ok(self):
        r = _make_report()
        output = self._render(r)
        assert "审查点命中" not in output

    def test_checkpoint_section_shown_when_hit(self):
        r = _make_report(blast=[_blast("high", "auth token missing", needs_human_review=False)])
        output = self._render(r)
        assert "审查点命中" in output

    def test_overflow_hint_when_more_than_5_high(self):
        highs = [_blast("high", f"issue {i}") for i in range(8)]
        r = _make_report(blast=highs)
        output = self._render(r)
        assert "+" in output and "条高风险" in output

    def test_blast_chain_shown_when_impact_paths(self):
        r = _make_report(blast=[_blast("high", "r")])
        r.impact_paths = [{"path": ["src/a.ts", "src/b.ts"], "risk": "high"}]
        output = self._render(r)
        assert "爆炸范围" in output
        assert "→" in output

    def test_medium_in_suggest_fix_section(self):
        r = _make_report(blast=[_blast("medium", "缺少错误处理")])
        output = self._render(r)
        assert "建议修复" in output

    def test_quiet_mode_shows_only_header_and_verdict(self):
        r = _make_report(blast=[_blast("high", "r")])
        output = self._render(r, quiet=True)
        assert "必须修复" not in output
        assert "审查点命中" not in output
```

- [ ] **Step 2: 确认测试失败**

```bash
python3 -m pytest tests/test_terminal_renderer.py::TestNewRenderRich -v 2>&1 | tail -20
```

期望：多个失败（旧结构仍显示修复队列表格、Verdict Panel 等）

- [ ] **Step 3: 重写 `_render_rich`**

将 `terminal_renderer.py` 中从 `def _render_rich` 到文件末尾的部分替换为：

```python
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
    chains = build_blast_chain(report)
    if chains:
        console.print(Rule("💥  爆炸范围", style="dim"))
        console.print()
        for chain in chains:
            console.print(Padding(Text(chain, style="dim cyan"), (0, 2)))
        console.print()

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
```

- [ ] **Step 4: 删除 `build_explosion_map` 函数**

从 `terminal_renderer.py` 中删除以下函数（包含其全部函数体）：
- `build_explosion_map`（约第 306-424 行，从 `def build_explosion_map` 到下一个函数定义之前）

- [ ] **Step 5: 确认新集成测试通过**

```bash
python3 -m pytest tests/test_terminal_renderer.py::TestNewRenderRich -v
```

期望：11 个全绿

- [ ] **Step 6: 清理引用已删除 `build_explosion_map` 的旧测试**

检查并删除 `tests/test_terminal_renderer.py` 中 `TestBuildBusinessTree` 里任何调用 `build_explosion_map` 的用例（若有）：

```bash
grep -n "build_explosion_map" tests/test_terminal_renderer.py
```

若有命中，删除对应行；若无，跳过。

- [ ] **Step 7: 全量回归**

```bash
python3 -m pytest -q --tb=no 2>&1 | tail -3
```

期望：全绿（数量可能因删除旧测试而减少，但无 FAIL）

---

## 验收标准

```bash
luna --staged
```

肉眼检查（按顺序）：
- [ ] 标题行合并项目信息到一条 Rule
- [ ] Verdict + 风险数同一行，无居中大框
- [ ] 高风险条目展开卡片，每条有 `$ luna fix N` 或 `$ luna detail N`
- [ ] 超过 5 条高风险时显示 `+ N 条高风险` 提示
- [ ] 中风险展开卡片，低风险单行
- [ ] 爆炸范围显示 `→` 链路
- [ ] 审查点命中区块只列有风险的；全部通过时整块不出现
- [ ] 无独立修复队列表格
- [ ] 无 Token 使用情况区块
- [ ] 无居中 Verdict Panel（无 `╭` 边框）
