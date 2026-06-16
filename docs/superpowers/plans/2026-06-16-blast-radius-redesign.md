# 爆炸范围影响链路重设计 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将"爆炸范围"输出重设计为每个 changed_symbol 独立成块的 Rich Tree，每节点带 file:line 和原因，让读者一眼看懂传播逻辑。

**Architecture:** 新增 `ChainNode` / `ImpactBlock` 两个数据类，以及 `_group_impact_paths(report)` 聚合函数负责数据准备；`_render_blast_section` 接收聚合结果驱动 Rich Tree 渲染。两层分离：数据逻辑可独立测试，渲染层只管展示。

**Tech Stack:** Python 3.10+, Rich (Tree/Rule/Padding/Text), pytest, dataclasses

---

## 文件变更清单

| 文件 | 操作 | 内容 |
|---|---|---|
| `terminal_renderer.py` | 修改 | 新增 `ChainNode`、`ImpactBlock` dataclass；新增 `_group_impact_paths`；替换 `_render_blast_section` |
| `tests/test_terminal_renderer.py` | 修改 | 新增 `TestGroupImpactPaths`、`TestRenderBlastSection` 两个测试类；更新 `test_blast_chain_shown_when_impact_paths` |

---

## Task 1: 添加 ChainNode 和 ImpactBlock dataclass，并为 _group_impact_paths 写失败测试

**Files:**
- Modify: `terminal_renderer.py` (在 `FixCandidate` 定义之后，约 line 304)
- Test: `tests/test_terminal_renderer.py`

- [ ] **Step 1: 在 terminal_renderer.py 的 FixCandidate 之后插入两个 dataclass**

在 `terminal_renderer.py` 第 304 行（`FixCandidate` 的 `suggestion` 字段之后的空行）插入：

```python
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
```

- [ ] **Step 2: 在 tests/test_terminal_renderer.py 末尾添加 TestGroupImpactPaths**

```python
class TestGroupImpactPaths:
    def test_empty_when_no_impact_paths(self):
        from terminal_renderer import _group_impact_paths
        r = _make_report()
        assert _group_impact_paths(r) == []

    def test_each_source_becomes_one_block(self):
        from terminal_renderer import _group_impact_paths
        r = _make_report()
        r.impact_paths = [
            {"path": ["src/a.ts", "src/b.ts"], "risk": "high",   "reason": "r1"},
            {"path": ["src/c.ts", "src/d.ts"], "risk": "medium", "reason": "r2"},
        ]
        blocks = _group_impact_paths(r)
        assert len(blocks) == 2
        assert blocks[0].symbol_name == "a.ts"
        assert blocks[1].symbol_name == "c.ts"

    def test_same_source_merged_into_one_block(self):
        from terminal_renderer import _group_impact_paths
        r = _make_report()
        r.impact_paths = [
            {"path": ["src/a.ts", "src/b.ts"], "risk": "high",   "reason": "r1"},
            {"path": ["src/a.ts", "src/c.ts"], "risk": "medium", "reason": "r2"},
        ]
        blocks = _group_impact_paths(r)
        assert len(blocks) == 1
        assert len(blocks[0].chains) == 2

    def test_block_risk_is_highest_across_paths(self):
        from terminal_renderer import _group_impact_paths
        r = _make_report()
        r.impact_paths = [
            {"path": ["src/a.ts", "src/b.ts"], "risk": "low",  "reason": "r"},
            {"path": ["src/a.ts", "src/c.ts"], "risk": "high", "reason": "r"},
        ]
        blocks = _group_impact_paths(r)
        assert blocks[0].risk == "high"

    def test_node_reason_matched_from_blast_item(self):
        from terminal_renderer import _group_impact_paths
        from phases.blast_radius import BlastRadiusItem
        item = BlastRadiusItem(
            file="src/b.ts", line=42, symbol="x",
            risk="high", confidence="medium", reason="auth check fails",
        )
        r = _make_report(blast=[item])
        r.impact_paths = [{"path": ["src/a.ts", "src/b.ts"], "risk": "high", "reason": "fallback"}]
        blocks = _group_impact_paths(r)
        node = blocks[0].chains[0][0]
        assert node.reason == "auth check fails"
        assert node.line == 42

    def test_node_reason_fallback_to_path_reason_on_leaf(self):
        from terminal_renderer import _group_impact_paths
        r = _make_report()
        r.impact_paths = [{"path": ["src/a.ts", "src/b.ts"], "risk": "medium", "reason": "path level reason"}]
        blocks = _group_impact_paths(r)
        node = blocks[0].chains[0][0]
        assert node.reason == "path level reason"

    def test_symbol_name_from_changed_symbols(self):
        from terminal_renderer import _group_impact_paths
        r = _make_report()
        r.changed_symbols = [{"name": "getUserById", "file": "src/a.ts"}]
        r.impact_paths = [{"path": ["src/a.ts", "src/b.ts"], "risk": "high", "reason": "r"}]
        blocks = _group_impact_paths(r)
        assert blocks[0].symbol_name == "getUserById"

    def test_single_node_paths_skipped(self):
        from terminal_renderer import _group_impact_paths
        r = _make_report()
        r.impact_paths = [{"path": ["src/a.ts"], "risk": "high", "reason": "r"}]
        blocks = _group_impact_paths(r)
        assert blocks == []
```

- [ ] **Step 3: 运行测试，确认全部失败（_group_impact_paths 未实现）**

```bash
python3 -m pytest tests/test_terminal_renderer.py::TestGroupImpactPaths -v
```

预期：全部 `FAILED` with `ImportError: cannot import name '_group_impact_paths'`

---

## Task 2: 实现 _group_impact_paths

**Files:**
- Modify: `terminal_renderer.py`（在 `build_blast_chain` 之前插入，约 line 325）

- [ ] **Step 1: 在 terminal_renderer.py 的 build_blast_chain 之前插入实现**

在 `build_blast_chain` 函数定义之前（当前约 line 325）插入：

```python
_RISK_ORDER = {"high": 0, "medium": 1, "low": 2}


def _group_impact_paths(report: "ReviewReport") -> list:
    """Group impact_paths by source symbol; annotate nodes from blast_radius_items."""
    valid_paths = [
        p for p in report.impact_paths
        if isinstance(p.get("path"), list) and len(p["path"]) >= 2
    ]
    if not valid_paths:
        return []

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
            key=lambda r: _RISK_ORDER.get(r, 2),
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
                    best = min(items, key=lambda it: _RISK_ORDER.get(it.risk, 2))
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
```

注意：`_RISK_ORDER` 已在文件中定义（line 93），删除这里新增的重复定义，直接引用已有的即可。检查文件中是否已存在 `_RISK_ORDER = {"high": 3, "medium": 2, "low": 1}`（注意方向相反）——若存在，在函数内部用局部变量 `_rv = {"high": 0, "medium": 1, "low": 2}` 替代，避免冲突。

实际插入代码（使用局部变量）：

```python
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
```

- [ ] **Step 2: 运行 TestGroupImpactPaths，确认全部通过**

```bash
python3 -m pytest tests/test_terminal_renderer.py::TestGroupImpactPaths -v
```

预期：8 个测试全部 `PASSED`

- [ ] **Step 3: 运行全量测试，确认无回归**

```bash
python3 -m pytest -q
```

预期：474 passed（或更多）

- [ ] **Step 4: commit**

```bash
git add terminal_renderer.py tests/test_terminal_renderer.py
git commit -m "feat: add ChainNode, ImpactBlock dataclasses and _group_impact_paths"
```

---

## Task 3: 为新版 _render_blast_section 写失败测试，并更新旧测试

**Files:**
- Test: `tests/test_terminal_renderer.py`

- [ ] **Step 1: 在文件末尾追加 TestRenderBlastSection 类**

```python
class TestRenderBlastSection:
    def _render(self, r):
        from io import StringIO
        from rich.console import Console
        from terminal_renderer import _render_blast_section
        buf = StringIO()
        con = Console(file=buf, width=120, no_color=True)
        _render_blast_section(con, r)
        return buf.getvalue()

    def test_empty_report_renders_nothing(self):
        r = _make_report()
        assert self._render(r) == ""

    def test_renders_symbol_name_as_root(self):
        r = _make_report()
        r.changed_symbols = [{"name": "getUserById", "file": "src/a.ts"}]
        r.impact_paths = [{"path": ["src/a.ts", "src/b.ts"], "risk": "high", "reason": "auth fails"}]
        output = self._render(r)
        assert "getUserById" in output

    def test_each_source_is_independent_block(self):
        r = _make_report()
        r.impact_paths = [
            {"path": ["src/a.ts", "src/b.ts"], "risk": "high",   "reason": "r1"},
            {"path": ["src/c.ts", "src/d.ts"], "risk": "medium", "reason": "r2"},
        ]
        output = self._render(r)
        assert "a.ts" in output
        assert "c.ts" in output

    def test_chain_node_shows_reason_from_blast_item(self):
        from phases.blast_radius import BlastRadiusItem
        item = BlastRadiusItem(
            file="src/b.ts", line=42, symbol="x",
            risk="high", confidence="medium", reason="auth check fails",
        )
        r = _make_report(blast=[item])
        r.impact_paths = [{"path": ["src/a.ts", "src/b.ts"], "risk": "high", "reason": "fallback"}]
        output = self._render(r)
        assert "b.ts" in output
        assert "auth check fails" in output

    def test_chain_count_in_header(self):
        r = _make_report()
        r.impact_paths = [
            {"path": ["src/a.ts", "src/b.ts"], "risk": "high",   "reason": "r1"},
            {"path": ["src/a.ts", "src/c.ts"], "risk": "medium", "reason": "r2"},
        ]
        output = self._render(r)
        assert "2 条" in output

    def test_fallback_shows_symbol_as_root(self):
        from phases.blast_radius import BlastRadiusItem
        item = BlastRadiusItem(
            file="src/b.ts", line=10, symbol="x",
            risk="high", confidence="medium", reason="breaks here",
        )
        r = _make_report(blast=[item])
        r.changed_symbols = [{"name": "myFunc", "file": "src/a.ts"}]
        output = self._render(r)
        assert "myFunc" in output
        assert "b.ts" in output

    def test_fallback_shows_reason(self):
        from phases.blast_radius import BlastRadiusItem
        item = BlastRadiusItem(
            file="src/b.ts", line=10, symbol="x",
            risk="high", confidence="medium", reason="breaks here",
        )
        r = _make_report(blast=[item])
        output = self._render(r)
        assert "breaks here" in output

    def test_section_header_says_影响链路(self):
        r = _make_report()
        r.impact_paths = [{"path": ["src/a.ts", "src/b.ts"], "risk": "high", "reason": "r"}]
        output = self._render(r)
        assert "影响链路" in output
```

- [ ] **Step 2: 在 TestNewRenderRich 中更新旧测试 test_blast_chain_shown_when_impact_paths**

找到该测试（约 line 533），将：

```python
def test_blast_chain_shown_when_impact_paths(self):
    r = _make_report(blast=[_blast("high", "r")])
    r.impact_paths = [{"path": ["src/a.ts", "src/b.ts"], "risk": "high"}]
    output = self._render(r)
    assert "爆炸范围" in output
    assert "→" in output
```

替换为：

```python
def test_blast_chain_shown_when_impact_paths(self):
    r = _make_report(blast=[_blast("high", "r")])
    r.impact_paths = [{"path": ["src/a.ts", "src/b.ts"], "risk": "high", "reason": ""}]
    output = self._render(r)
    assert "影响链路" in output
    assert "b.ts" in output
```

- [ ] **Step 3: 运行新测试，确认 TestRenderBlastSection 全部失败**

```bash
python3 -m pytest tests/test_terminal_renderer.py::TestRenderBlastSection -v
```

预期：新测试 `FAILED`（因为 `_render_blast_section` 还是旧实现）

---

## Task 4: 替换 _render_blast_section 实现

**Files:**
- Modify: `terminal_renderer.py`（替换 `_render_blast_section` 函数体，约 line 369）

- [ ] **Step 1: 用新实现完整替换 _render_blast_section**

将当前 `_render_blast_section` 函数（从 `def _render_blast_section` 到其结尾的 `console.print()`）整体替换为：

```python
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
```

- [ ] **Step 2: 运行 TestRenderBlastSection，确认全部通过**

```bash
python3 -m pytest tests/test_terminal_renderer.py::TestRenderBlastSection -v
```

预期：8 个测试全部 `PASSED`

- [ ] **Step 3: 运行全量测试，确认无回归**

```bash
python3 -m pytest -q
```

预期：482 passed（474 原有 + 8 新增）

- [ ] **Step 4: commit**

```bash
git add terminal_renderer.py tests/test_terminal_renderer.py
git commit -m "feat: redesign blast radius as per-symbol impact chain trees"
```

---

## 自检清单（已执行）

- [x] **spec 覆盖：** 每个 changed_symbol 独立块 ✓；节点带 file:line + reason ✓；blast_items fallback ✓；审查点命中不受影响 ✓；build_blast_chain 保留不变 ✓
- [x] **无 placeholder：** 所有步骤均含完整代码
- [x] **类型一致性：** `ChainNode.file/line/reason/risk`、`ImpactBlock.symbol_name/risk/chains` 在 Task 1 定义，Task 2/4 使用，字段名一致
- [x] **测试完整性：** _group_impact_paths 8 个单元测试；_render_blast_section 8 个集成测试；旧测试已更新
