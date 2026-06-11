# ✅ 已完成 · Caller Context Import Filter Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 过滤 `grep_call_sites` 返回结果中的 import 行和纯类型注解行，让 caller_contexts 只包含真实调用点。

**Architecture:** 在 `phases/caller_context.py` 的 `grep_call_sites` 函数里，在现有注释行过滤之后追加两条启发式规则：① stripped 以 import/from/using/use/require 等前缀开头的行跳过；② 行里没有 `symbol(` 也没有 `symbol.`、但有类型上下文标记（`: symbol`、`-> symbol`、`[symbol` 等）的行跳过。

**Tech Stack:** Python 3.11+, pytest

---

## 文件地图

| 操作 | 路径 | 职责 |
|------|------|------|
| Modify | `phases/caller_context.py` | 新增 `_IMPORT_PREFIXES` 常量，`grep_call_sites` 追加两条过滤规则 |
| Modify | `tests/test_caller_context.py` | 追加 7 个新测试 |

---

## Task 1：过滤 import 行

**Files:**
- Modify: `phases/caller_context.py`
- Modify: `tests/test_caller_context.py`

- [ ] **写失败测试**

在 `tests/test_caller_context.py` 的 `# grep_call_sites` 区块末尾追加：

```python
def test_grep_excludes_python_import_line(tmp_path):
    f = tmp_path / "app.py"
    f.write_text("from graph import build_graph\nresult = build_graph('.')\n")
    hits = grep_call_sites("build_graph", str(tmp_path), ignore_dirs=[], self_file=None)
    lines = [h[1] for h in hits if h[0].endswith("app.py")]
    assert lines == [2]  # import 行（第1行）不应出现


def test_grep_excludes_ts_import_line(tmp_path):
    f = tmp_path / "main.ts"
    f.write_text(
        "import { buildGraph } from './graph';\n"
        "import type { buildGraph } from './types';\n"
        "const g = buildGraph();\n"
    )
    hits = grep_call_sites("buildGraph", str(tmp_path), ignore_dirs=[], self_file=None)
    lines = [h[1] for h in hits if h[0].endswith("main.ts")]
    assert lines == [3]  # 只保留第3行真实调用
```

- [ ] **确认测试失败**

```bash
python3 -m pytest tests/test_caller_context.py::test_grep_excludes_python_import_line tests/test_caller_context.py::test_grep_excludes_ts_import_line -v
```

预期：FAIL（目前 import 行没被过滤，第1行也会出现在结果里）

- [ ] **在 `phases/caller_context.py` 新增常量并更新过滤逻辑**

在 `_COMMENT_PREFIXES` 常量下面新增：

```python
_IMPORT_PREFIXES = (
    "import ",   # Python: import x / import x as y
                 # TS/JS/Java: import X from '...' / import com.xxx.X;
    "from ",     # Python: from x import y
    "import{",   # TS/JS 无空格: import{X} from '...'
    "using ",    # C#: using X;
    "use ",      # PHP: use X;
    "require ",  # Ruby: require 'x'
)
```

在 `grep_call_sites` 的过滤循环里，紧接注释行过滤之后追加：

```python
        # Exclude import lines
        if stripped.startswith(_IMPORT_PREFIXES):
            continue
```

完整循环体此时如下（供核对）：

```python
        # Exclude self file
        if self_norm and os.path.normpath(file_path) == self_norm:
            continue

        # Exclude comment lines
        stripped = content.lstrip()
        if stripped.startswith(_COMMENT_PREFIXES):
            continue

        # Exclude import lines
        if stripped.startswith(_IMPORT_PREFIXES):
            continue

        hits.append((file_path, line_no))
```

- [ ] **确认测试通过**

```bash
python3 -m pytest tests/test_caller_context.py::test_grep_excludes_python_import_line tests/test_caller_context.py::test_grep_excludes_ts_import_line -v
```

预期：2 passed

- [ ] **全套回归**

```bash
python3 -m pytest -q
```

预期：全绿，无回归

- [ ] **暂停，等用户确认是否提交**

---

## Task 2：过滤纯类型注解行

**Files:**
- Modify: `phases/caller_context.py`
- Modify: `tests/test_caller_context.py`

- [ ] **写失败测试**

在 `tests/test_caller_context.py` 追加：

```python
def test_grep_excludes_type_annotation_parameter(tmp_path):
    f = tmp_path / "app.py"
    f.write_text(
        "def process(g: build_graph) -> None:\n"
        "    pass\n"
        "result = build_graph('.')\n"
    )
    hits = grep_call_sites("build_graph", str(tmp_path), ignore_dirs=[], self_file=None)
    lines = [h[1] for h in hits if h[0].endswith("app.py")]
    assert lines == [3]  # 第1行是纯类型注解，不应出现


def test_grep_excludes_return_type_annotation(tmp_path):
    f = tmp_path / "app.py"
    f.write_text(
        "def factory() -> build_graph:\n"
        "    return build_graph('.')\n"
    )
    hits = grep_call_sites("build_graph", str(tmp_path), ignore_dirs=[], self_file=None)
    lines = [h[1] for h in hits if h[0].endswith("app.py")]
    assert lines == [2]  # 第1行 -> 类型注解不应出现，第2行实例化应保留


def test_grep_keeps_instantiation_line(tmp_path):
    f = tmp_path / "app.py"
    f.write_text("graph = build_graph('.')\n")
    hits = grep_call_sites("build_graph", str(tmp_path), ignore_dirs=[], self_file=None)
    assert any(h[0].endswith("app.py") and h[1] == 1 for h in hits)


def test_grep_keeps_attribute_access_line(tmp_path):
    f = tmp_path / "app.py"
    f.write_text("nodes = build_graph.nodes\n")
    hits = grep_call_sites("build_graph", str(tmp_path), ignore_dirs=[], self_file=None)
    assert any(h[0].endswith("app.py") and h[1] == 1 for h in hits)


def test_grep_keeps_isinstance_check(tmp_path):
    # isinstance 没有 symbol( 或 symbol.，但也没有类型上下文标记 → 保留
    f = tmp_path / "app.py"
    f.write_text("assert isinstance(g, build_graph)\n")
    hits = grep_call_sites("build_graph", str(tmp_path), ignore_dirs=[], self_file=None)
    assert any(h[0].endswith("app.py") and h[1] == 1 for h in hits)
```

- [ ] **确认测试失败**

```bash
python3 -m pytest tests/test_caller_context.py::test_grep_excludes_type_annotation_parameter tests/test_caller_context.py::test_grep_excludes_return_type_annotation tests/test_caller_context.py::test_grep_keeps_instantiation_line tests/test_caller_context.py::test_grep_keeps_attribute_access_line tests/test_caller_context.py::test_grep_keeps_isinstance_check -v
```

预期：前两个 FAIL，后三个已 PASS（后三个测试现有代码就能过）

- [ ] **在 `grep_call_sites` 里追加纯类型注解过滤**

紧接 import 行过滤之后追加（`symbol` 变量从函数参数取得）：

```python
        # Exclude pure type-annotation lines (no real call or attribute access)
        is_real_usage = f"{symbol}(" in content or f"{symbol}." in content
        if not is_real_usage:
            type_markers = (
                f": {symbol}",
                f"->{symbol}",
                f"-> {symbol}",
                f"[{symbol}",
                f"| {symbol}",
                f"{symbol}]",
                f"{symbol},",
            )
            if any(m in content for m in type_markers):
                continue
```

完整过滤循环体此时如下（供核对）：

```python
        # Exclude self file
        if self_norm and os.path.normpath(file_path) == self_norm:
            continue

        # Exclude comment lines
        stripped = content.lstrip()
        if stripped.startswith(_COMMENT_PREFIXES):
            continue

        # Exclude import lines
        if stripped.startswith(_IMPORT_PREFIXES):
            continue

        # Exclude pure type-annotation lines (no real call or attribute access)
        is_real_usage = f"{symbol}(" in content or f"{symbol}." in content
        if not is_real_usage:
            type_markers = (
                f": {symbol}",
                f"->{symbol}",
                f"-> {symbol}",
                f"[{symbol}",
                f"| {symbol}",
                f"{symbol}]",
                f"{symbol},",
            )
            if any(m in content for m in type_markers):
                continue

        hits.append((file_path, line_no))
```

- [ ] **确认测试通过**

```bash
python3 -m pytest tests/test_caller_context.py -v
```

预期：全部通过（含 Task 1 的测试）

- [ ] **全套回归**

```bash
python3 -m pytest -q
```

预期：全绿，无回归

- [ ] **暂停，等用户确认是否提交**
