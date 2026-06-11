# Caller Context Import Filter — 过滤 import + 纯类型注解行

**日期：** 2026-06-11
**背景：** Luna 自审 PR（feat/caller-context）时，`FixCandidate` 的 caller_contexts 里充斥着 `from terminal_renderer import FixCandidate` 这类 import 行，LLM 读不出调用方怎么用这个符号，诚实地标了 high + low confidence。根本原因：grep 找到的行包含 symbol 名，但属于"引用而非调用"。

---

## 目标

让 `grep_call_sites` 只返回"调用方真实使用该符号"的行，过滤掉两类噪音：
1. **import 行** — 只是把符号引入作用域，LLM 从中得不到任何调用信息
2. **纯类型注解行** — 符号出现在类型标注位置（`: X`、`-> X`、`list[X]`），不涉及运行时调用

---

## 不做的事

- 不引入 AST 解析（保持 grep + 启发式的定位）
- 不区分语言做精确过滤（方案 B，语言无关启发式）
- 不过滤 `isinstance(x, Symbol)` 等边界情况（这类行对 LLM 有价值，留着）

---

## 设计

### 改动范围

**唯一改动文件：** `phases/caller_context.py`

- 新增常量 `_IMPORT_PREFIXES`
- `grep_call_sites` 的过滤循环里追加两条规则
- 测试文件：`tests/test_caller_context.py`

### 规则 1：Import 行过滤

```python
_IMPORT_PREFIXES = (
    "import ",    # Python: import x / import x as y
                  # TS/JS/Java: import X from '...' / import com.xxx.X;
    "from ",      # Python: from x import y
    "import{",    # TS/JS 无空格写法: import{X} from '...'
    "using ",     # C#: using X;
    "use ",       # PHP: use X;
    "require ",   # Ruby: require 'x'
)
```

判断：`stripped.startswith(_IMPORT_PREFIXES)` → 跳过。

### 规则 2：纯类型注解行过滤

同时满足以下 **3 个条件** 时跳过：

| 条件 | 说明 |
|------|------|
| `f"{symbol}(" not in content` | 不是调用/实例化 |
| `f"{symbol}." not in content` | 不是属性/方法访问 |
| 行里含类型上下文标记 | `: symbol`、`-> symbol`、`[symbol`、`\| symbol`、`symbol]`、`symbol,` 中至少一个 |

实现用字符串 `in` 操作，不用正则，与现有代码风格一致。

```python
_TYPE_CONTEXT_MARKERS = (
    f": {symbol}",
    f"-> {symbol}",
    f"[{symbol}",
    f"| {symbol}",
    f"{symbol}]",
    f"{symbol},",
)

is_real_call = f"{symbol}(" in content or f"{symbol}." in content
has_type_context = any(m in content for m in _TYPE_CONTEXT_MARKERS)
if not is_real_call and has_type_context:
    continue
```

### 过滤顺序（在现有注释过滤之后）

```
原有：排除 self_file
原有：排除注释行（#、//、/*、*）
新增：排除 import 行
新增：排除纯类型注解行
```

---

## 测试覆盖

新增测试（追加到 `tests/test_caller_context.py`）：

| 测试名 | 验证点 |
|--------|--------|
| `test_grep_excludes_python_import_line` | `from x import Symbol` 被过滤 |
| `test_grep_excludes_ts_import_line` | `import { Symbol } from './x'` 被过滤 |
| `test_grep_excludes_type_annotation_line` | `def foo(x: Symbol):` 被过滤 |
| `test_grep_excludes_return_type_annotation` | `def foo() -> Symbol:` 被过滤 |
| `test_grep_keeps_instantiation` | `s = Symbol(...)` 被保留 |
| `test_grep_keeps_attribute_access` | `Symbol.method()` 被保留 |
| `test_grep_keeps_isinstance_check` | `isinstance(x, Symbol)` 被保留（边界情况） |

---

## 预期效果

`FixCandidate` 这类案例：
- 过滤掉：`from terminal_renderer import FixCandidate`（import 行）
- 保留：`candidates.append(FixCandidate(file=..., line=...))` （实例化）
- 保留：`candidate: FixCandidate = build(...)` 如果行里有 `FixCandidate(`
- 过滤掉：`def apply_fix(candidate: FixCandidate) -> None:` （纯类型注解，无调用）

LLM 看到的 snippet 将是真实实例化/调用代码，能据此判断调用方是否依赖被改字段。
