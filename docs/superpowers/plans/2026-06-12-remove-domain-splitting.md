# Remove Domain Splitting 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 删除基于手动配置的域分块逻辑，让 adversarial verify 无条件运行（只要有 uncertain-high 发现），消除 `cfg.domains` 门控导致的功能从未实际运行的 bug。

**Architecture:** 三步走：(1) 把 `filter_diff_for_files` 移入 `adversarial_verifier.py`，简化 `build_adversarial_context` 签名为 `(diff, finding_files, context_pack)`；(2) 删除 `domain_classifier.py` 全文和 `config.py` 里的 `DomainEntry`/`domains`/`max_parallel_domains`；(3) 修 `luna.py` 去掉 `cfg.domains` 门控，adversarial pass 改为直接对所有 uncertain-high 发现做一次反驳。

**Tech Stack:** Python 3.11+，无新依赖

---

## 文件结构

| 文件 | 动作 | 变化 |
|------|------|------|
| `phases/adversarial_verifier.py` | 修改 | 内联 `filter_diff_for_files`；简化 `build_adversarial_context` 签名 |
| `phases/domain_classifier.py` | **删除** | 全部逻辑已无用（filter_diff 移走，classify/group 删除） |
| `config.py` | 修改 | 删 `DomainEntry`、`Config.domains`、`ReviewConfig.max_parallel_domains`、load_config 解析段 |
| `luna.py` | 修改 | 去掉 `cfg.domains` 门控；adversarial pass 改为单次；phase_list 无条件加 adversarial |
| `tests/test_adversarial_verifier.py` | 修改 | 更新 `build_adversarial_context` 测试签名；新增 filter_diff 测试（从 test_domain_classifier 迁入） |
| `tests/test_adversarial_wiring.py` | 修改 | 去掉 `cfg.domains` 配置；断言无条件触发 |
| `tests/test_config_domains.py` | **删除** | 对应功能已删 |
| `tests/test_domain_classifier.py` | **删除** | filter_diff 测试迁入 test_adversarial_verifier |

---

## Task 1：简化 adversarial_verifier.py

**Files:**
- Modify: `phases/adversarial_verifier.py`
- Modify: `tests/test_adversarial_verifier.py`

把 `filter_diff_for_files` 从 domain_classifier 复制进来，改掉 `build_adversarial_context` 签名，更新测试。**本 Task 结束后 domain_classifier 仍存在，不要提前删。**

- [ ] **Step 1：更新 `phases/adversarial_verifier.py`**

用以下内容完整替换该文件（内联了 `filter_diff_for_files`，简化了 `build_adversarial_context`，删掉了 `domain_name`/`domain_syms` 参数）：

```python
from __future__ import annotations
import json
import re
from typing import TYPE_CHECKING

from api_client import call_claude

if TYPE_CHECKING:
    from phases.blast_radius import BlastRadiusItem
    from phases.context_pack import ContextPack
    from config import Config

_SYSTEM = """\
你是代码审查质疑者。你将收到一批"high 风险但置信度非 high"的审查发现，以及相关代码上下文。
你的任务是：逐条尝试证明每个发现是误报（false positive）。

判断原则：
- 调用方代码中没有使用改动的属性或返回值 → 不是真实风险（confirmed: false）
- 改动的符号在项目内没有调用方 → 不是真实风险
- 风险理由与代码上下文明显不符 → 不是真实风险
- 找不到足够的反驳理由 → 保留（confirmed: true）

以 JSON 数组输出，每个元素：
- index: 原始 finding 的序号（整数）
- confirmed: 是否确认为真实风险（bool）
- reason: 判断理由（中文，一句话）

只输出 JSON 数组，不要其他内容。"""


def filter_diff_for_files(diff: str, files: set[str]) -> str:
    """Return only diff hunks whose b/ path matches a file in `files`."""
    if not files:
        return ""
    result: list[str] = []
    lines = diff.split("\n")
    i = 0
    file_header: list[str] = []
    active = False
    while i < len(lines):
        line = lines[i]
        if line.startswith("diff --git "):
            parts = line.split(" b/", 1)
            diff_path = parts[1].strip() if len(parts) == 2 else ""
            active = any(diff_path == f or diff_path.endswith("/" + f) for f in files)
            file_header = [line]
            i += 1
            while i < len(lines) and not lines[i].startswith("@@") and not lines[i].startswith("diff --git "):
                file_header.append(lines[i])
                i += 1
            continue
        if active:
            if file_header:
                result.extend(file_header)
                file_header = []
            result.append(line)
        i += 1
    return "\n".join(result)


def build_adversarial_context(
    diff: str,
    finding_files: set[str],
    context_pack: "ContextPack",
) -> str:
    """Build adversarial context from diff + caller_contexts scoped to finding files."""
    finding_sym_names = {
        s.symbol for s in context_pack.changed_symbols
        if s.file in finding_files
    }
    filtered_diff = filter_diff_for_files(diff, finding_files)

    caller_lines: list[str] = []
    for sc in context_pack.caller_contexts:
        if sc.symbol in finding_sym_names:
            caller_lines.append(f"symbol={sc.symbol}; callers={sc.total_count}")
            for c in sc.callers[:3]:
                caller_lines.append(f"  {c.file}:{c.line}  {c.snippet}")

    callers_text = "\n".join(caller_lines[:30]) if caller_lines else "（无）"
    return (
        f"## 调用方上下文\n{callers_text}\n\n"
        f"## 相关 diff\n```diff\n{filtered_diff[:4000]}\n```"
    )


def adversarial_verify(
    items: list["BlastRadiusItem"],
    context_snippet: str,
    config: "Config | None",
) -> list["BlastRadiusItem"]:
    if not items:
        return []

    to_verify = [(i, item) for i, item in enumerate(items) if item.risk == "high" and item.confidence != "high"]
    if not to_verify:
        return list(items)

    findings_text = json.dumps(
        [{"index": i, "file": item.file, "symbol": item.symbol, "reason": item.reason, "confidence": item.confidence}
         for i, item in to_verify],
        ensure_ascii=False, indent=2,
    )
    user = (
        f"## 待验证 findings\n\n```json\n{findings_text}\n```\n\n"
        f"## 相关代码上下文\n\n```\n{context_snippet}\n```"
    )

    try:
        raw = call_claude(_SYSTEM, user, config)
        match = re.search(r"\[.*\]", raw, re.DOTALL)
        verdicts = json.loads(match.group()) if match else []
    except Exception:
        return list(items)

    refuted = {v["index"] for v in verdicts if isinstance(v, dict) and not v.get("confirmed", True)}
    verify_indices = {i for i, _ in to_verify}
    return [item for i, item in enumerate(items) if not (i in verify_indices and i in refuted)]
```

- [ ] **Step 2：更新 `tests/test_adversarial_verifier.py`**

用以下内容完整替换该文件：

```python
import json
from unittest.mock import patch
from phases.blast_radius import BlastRadiusItem
from phases.context_pack import ContextPack, build_context_pack
from phases.symbol_locator import ChangedSymbol
from phases.caller_context import CallerSnippet, SymbolCallers
from phases.adversarial_verifier import (
    adversarial_verify,
    build_adversarial_context,
    filter_diff_for_files,
)
from config import Config


def _item(risk="high", confidence="medium", symbol="foo", file="src/a.ts") -> BlastRadiusItem:
    return BlastRadiusItem(file=file, line=1, symbol=symbol, risk=risk, confidence=confidence, reason="可能影响支付")


def _sym(file="src/a.ts", symbol="foo") -> ChangedSymbol:
    return ChangedSymbol(file=file, symbol=symbol, symbol_type="function", start_line=1, change_type="modified")


def _pack_with_callers(sym: ChangedSymbol, snippet: str = "foo()") -> ContextPack:
    pack = build_context_pack([sym], [], related_rules=[], related_tests=[])
    pack.caller_contexts = [
        SymbolCallers(symbol=sym.symbol, callers=[
            CallerSnippet(file="src/caller.ts", line=10, snippet=snippet, language="typescript")
        ], total_count=1)
    ]
    return pack


def _mock_llm(response: str):
    return patch("phases.adversarial_verifier.call_claude", return_value=response)


# --- filter_diff_for_files ---

DIFF_TWO_FILES = (
    "diff --git a/src/a.ts b/src/a.ts\n"
    "index 0000000..1111111 100644\n"
    "--- a/src/a.ts\n"
    "+++ b/src/a.ts\n"
    "@@ -1 +1 @@\n-old\n+new\n"
    "diff --git a/src/b.ts b/src/b.ts\n"
    "index 0000000..2222222 100644\n"
    "--- a/src/b.ts\n"
    "+++ b/src/b.ts\n"
    "@@ -1 +1 @@\n-old\n+new\n"
)


def test_filter_diff_returns_only_matching_file():
    filtered = filter_diff_for_files(DIFF_TWO_FILES, {"src/b.ts"})
    assert "b/src/b.ts" in filtered
    assert "b/src/a.ts" not in filtered


def test_filter_diff_no_match_returns_empty():
    assert filter_diff_for_files(DIFF_TWO_FILES, {"src/c.ts"}) == ""


def test_filter_diff_empty_files_returns_empty():
    assert filter_diff_for_files(DIFF_TWO_FILES, set()) == ""


# --- adversarial_verify ---

def test_confirmed_finding_survives():
    finding = _item()
    resp = json.dumps([{"index": 0, "confirmed": True, "reason": "确实影响支付"}])
    with _mock_llm(resp):
        result = adversarial_verify([finding], context_snippet="pay(amount)", config=None)
    assert len(result) == 1 and result[0].symbol == "foo"


def test_refuted_finding_is_removed():
    finding = _item()
    resp = json.dumps([{"index": 0, "confirmed": False, "reason": "调用方不使用返回值"}])
    with _mock_llm(resp):
        result = adversarial_verify([finding], context_snippet="", config=None)
    assert result == []


def test_high_confidence_skips_llm():
    finding = _item(confidence="high")
    with patch("phases.adversarial_verifier.call_claude") as mock_llm:
        result = adversarial_verify([finding], context_snippet="", config=None)
    mock_llm.assert_not_called()
    assert len(result) == 1


def test_low_risk_skips_llm():
    finding = _item(risk="low", confidence="low")
    with patch("phases.adversarial_verifier.call_claude") as mock_llm:
        result = adversarial_verify([finding], context_snippet="", config=None)
    mock_llm.assert_not_called()
    assert len(result) == 1


def test_no_json_array_in_response_keeps_all():
    finding = _item()
    with _mock_llm("not valid json"):
        result = adversarial_verify([finding], context_snippet="", config=None)
    assert len(result) == 1


def test_llm_exception_keeps_all():
    finding = _item()
    with patch("phases.adversarial_verifier.call_claude", side_effect=RuntimeError("network error")):
        result = adversarial_verify([finding], context_snippet="", config=None)
    assert len(result) == 1


def test_empty_input_returns_empty():
    assert adversarial_verify([], context_snippet="", config=None) == []


def test_prompt_contains_context():
    finding = _item()
    calls = []
    def fake_call(system, user, config):
        calls.append(user)
        return json.dumps([{"index": 0, "confirmed": True, "reason": "保留"}])
    with patch("phases.adversarial_verifier.call_claude", side_effect=fake_call):
        adversarial_verify([finding], context_snippet="caller: pay(amount)", config=None)
    assert "caller: pay(amount)" in calls[0]


# --- build_adversarial_context ---

def test_build_context_contains_caller_snippet():
    sym = _sym()
    pack = _pack_with_callers(sym, snippet="pay(amount)")
    ctx = build_adversarial_context("diff content", {sym.file}, pack)
    assert "pay(amount)" in ctx


def test_build_context_contains_filtered_diff():
    sym = _sym(file="src/a.ts")
    pack = build_context_pack([sym], [], related_rules=[], related_tests=[])
    diff = (
        "diff --git a/src/a.ts b/src/a.ts\n@@ -1 +1 @@\n-old\n+new\n"
        "diff --git a/src/b.ts b/src/b.ts\n@@ -1 +1 @@\n-old\n+new\n"
    )
    ctx = build_adversarial_context(diff, {"src/a.ts"}, pack)
    assert "src/a.ts" in ctx
    assert "src/b.ts" not in ctx


def test_build_context_excludes_unrelated_callers():
    sym_a = _sym(file="src/a.ts", symbol="funcA")
    sym_b = _sym(file="src/b.ts", symbol="funcB")
    pack = build_context_pack([sym_a, sym_b], [], related_rules=[], related_tests=[])
    pack.caller_contexts = [
        SymbolCallers(symbol="funcA", callers=[
            CallerSnippet(file="src/x.ts", line=1, snippet="funcA()", language="typescript")
        ], total_count=1),
        SymbolCallers(symbol="funcB", callers=[], total_count=0),
    ]
    # finding_files 只包含 src/a.ts → 只应看到 funcA 的 callers
    ctx = build_adversarial_context("", {"src/a.ts"}, pack)
    assert "funcA" in ctx
    assert "funcB" not in ctx


def test_build_context_no_callers_shows_placeholder():
    sym = _sym()
    pack = build_context_pack([sym], [], related_rules=[], related_tests=[])
    ctx = build_adversarial_context("", {sym.file}, pack)
    assert "（无）" in ctx
```

- [ ] **Step 3：运行测试，预期全部通过**

```bash
cd /Users/wangyinlong/luna && python3 -m pytest tests/test_adversarial_verifier.py -v
```

Expected: 15 passed（3 filter_diff + 8 adversarial_verify + 4 build_context）

- [ ] **Step 4：全量回归（此时 domain_classifier 仍存在，应无回归）**

```bash
cd /Users/wangyinlong/luna && python3 -m pytest --tb=short -q
```

Expected: 全部通过

- [ ] **Step 5：commit**

```bash
cd /Users/wangyinlong/luna
git add phases/adversarial_verifier.py tests/test_adversarial_verifier.py
git commit -m "refactor: inline filter_diff, simplify build_adversarial_context signature"
```

---

## Task 2：删除 domain_classifier + config 域配置

**Files:**
- Delete: `phases/domain_classifier.py`
- Modify: `config.py`
- Delete: `tests/test_config_domains.py`
- Delete: `tests/test_domain_classifier.py`

**注意：** 此时 luna.py 还在用 `domain_classifier`（`classify_symbols_by_domain`、`group_findings_by_domain`），删文件会导致全量测试失败——这是预期的，Task 3 会修掉。

- [ ] **Step 1：删除三个文件**

```bash
cd /Users/wangyinlong/luna
rm phases/domain_classifier.py
rm tests/test_config_domains.py
rm tests/test_domain_classifier.py
```

- [ ] **Step 2：修改 `config.py`——删除三处域相关代码**

**删除 `ReviewConfig` 里的 `max_parallel_domains`**（约第 30 行）：

删前：
```python
@dataclass
class ReviewConfig:
    language: str = "zh"
    project_type: str = "auto"
    confirm_before_fix: bool = True
    max_diff_chars: int = 120_000
    apply_enabled: bool = False
    max_parallel_domains: int = 3
```

删后：
```python
@dataclass
class ReviewConfig:
    language: str = "zh"
    project_type: str = "auto"
    confirm_before_fix: bool = True
    max_diff_chars: int = 120_000
    apply_enabled: bool = False
```

**删除 `DomainEntry` 整个 dataclass**（约第 74-76 行）：

删除：
```python
@dataclass
class DomainEntry:
    name: str
    patterns: list[str] = field(default_factory=list)
```

**删除 `Config` 里的 `domains` 字段**（约第 89 行）：

删前：
```python
    api_change: APIChangeConfig = field(default_factory=APIChangeConfig)
    domains: list[DomainEntry] = field(default_factory=list)
```

删后：
```python
    api_change: APIChangeConfig = field(default_factory=APIChangeConfig)
```

**删除 `load_config()` 里的 domains 解析段**（约第 130-138 行）：

删除整段：
```python
    if raw_domains := raw.get("domains"):
        cfg.domains = [
            DomainEntry(
                name=d["name"],
                patterns=d.get("patterns", []),
            )
            for d in raw_domains
            if isinstance(d, dict) and "name" in d
        ]
```

- [ ] **Step 3：确认 config.py 里没有 DomainEntry 残留**

```bash
cd /Users/wangyinlong/luna && grep -n "DomainEntry\|domains\|max_parallel" config.py
```

Expected: 无输出

- [ ] **Step 4：运行全量测试（预期有失败——luna.py 还在引用已删的 domain_classifier）**

```bash
cd /Users/wangyinlong/luna && python3 -m pytest --tb=short -q 2>&1 | tail -20
```

Expected: ImportError 或 ModuleNotFoundError（`phases.domain_classifier`），其余测试通过

- [ ] **Step 5：commit**

```bash
cd /Users/wangyinlong/luna
git add -A
git commit -m "refactor: remove domain_classifier and DomainEntry config"
```

---

## Task 3：修复 luna.py 接线

**Files:**
- Modify: `luna.py`
- Modify: `tests/test_adversarial_wiring.py`

去掉 `cfg.domains` 门控，adversarial pass 改为对所有 uncertain-high 发现做一次反驳，phase_list 无条件加 adversarial。

- [ ] **Step 1：找到需要修改的三处位置**

```bash
cd /Users/wangyinlong/luna && grep -n "cfg.domains\|adversarial\|_run_frontend\|_phase_list" luna.py | head -20
```

应看到：
- 约 181 行：`_phase_list += [...("blast", ...)]`（frontend 分支）
- 约 183 行：`_phase_list += [("blast", ...)]`（else 分支）
- 约 190-191 行：`if cfg.domains: _phase_list += [("adversarial", ...)]`
- 约 381 行：`if cfg.domains and context_pack is not None and blast_items:`

- [ ] **Step 2：更新 phase_list——把 adversarial 无条件跟在 blast 后面**

找到（约 180-183 行）：
```python
    if _run_frontend:
        _phase_list += [("frontend_graph", "构建前端代码图谱"), ("hybrid_ctx", "混合语义检索"), ("blast", "爆炸范围分析")]
    elif phase in (None, "blast"):
        _phase_list += [("blast", "爆炸范围分析")]
```

替换为：
```python
    if _run_frontend:
        _phase_list += [("frontend_graph", "构建前端代码图谱"), ("hybrid_ctx", "混合语义检索"), ("blast", "爆炸范围分析"), ("adversarial", "反驳验证")]
    elif phase in (None, "blast"):
        _phase_list += [("blast", "爆炸范围分析"), ("adversarial", "反驳验证")]
```

然后删掉（约 190-191 行）：
```python
    if cfg.domains:
        _phase_list += [("adversarial", "反驳验证")]
```

- [ ] **Step 3：替换 adversarial pass 逻辑**

找到（约 381-408 行）：
```python
        if cfg.domains and context_pack is not None and blast_items:
            _begin("adversarial")
            try:
                from phases.domain_classifier import (
                    classify_symbols_by_domain as _classify,
                    group_findings_by_domain as _group,
                )
                from phases.adversarial_verifier import (
                    adversarial_verify as _adv_verify,
                    build_adversarial_context as _build_ctx,
                )
                _domain_map = _classify(context_pack.changed_symbols, cfg.domains)
                _findings_by_domain = _group(blast_items, _domain_map)
                _verified: list = []
                for _dname, _ditems in _findings_by_domain.items():
                    _uncertain = [i for i in _ditems if i.risk == "high" and i.confidence != "high"]
                    _certain = [i for i in _ditems if not (i.risk == "high" and i.confidence != "high")]
                    if _uncertain:
                        _ctx = _build_ctx(_dname, diff, _domain_map.get(_dname, []), context_pack)
                        _uncertain = _adv_verify(_uncertain, _ctx, cfg)
                    _verified.extend(_certain)
                    _verified.extend(_uncertain)
                blast_items = _verified
            except Exception as _adv_err:
                if _rcon:
                    _rcon.print(f"[dim yellow]⚠ adversarial_verify 降级：{_adv_err}[/dim yellow]")
            finally:
                _finish("adversarial")
```

替换为：
```python
        if context_pack is not None and blast_items:
            _begin("adversarial")
            try:
                from phases.adversarial_verifier import (
                    adversarial_verify as _adv_verify,
                    build_adversarial_context as _build_ctx,
                )
                _uncertain = [i for i in blast_items if i.risk == "high" and i.confidence != "high"]
                _certain = [i for i in blast_items if not (i.risk == "high" and i.confidence != "high")]
                if _uncertain:
                    _files = {i.file for i in _uncertain}
                    _ctx = _build_ctx(diff, _files, context_pack)
                    _uncertain = _adv_verify(_uncertain, _ctx, cfg)
                blast_items = _certain + _uncertain
            except Exception as _adv_err:
                if _rcon:
                    _rcon.print(f"[dim yellow]⚠ adversarial_verify 降级：{_adv_err}[/dim yellow]")
            finally:
                _finish("adversarial")
```

- [ ] **Step 4：更新 `tests/test_adversarial_wiring.py`**

用以下内容完整替换该文件（去掉 `cfg.domains`，改为无条件触发）：

```python
import json
from unittest.mock import patch
from config import Config
from phases.blast_radius import BlastRadiusItem
from phases.context_pack import build_context_pack
from phases.symbol_locator import ChangedSymbol
from phases.adversarial_verifier import (
    adversarial_verify,
    build_adversarial_context,
    filter_diff_for_files,
)


PRIVATE_DIFF = (
    "diff --git a/src/private/a.ts b/src/private/a.ts\n"
    "index 0000000..1111111 100644\n"
    "--- a/src/private/a.ts\n"
    "+++ b/src/private/a.ts\n"
    "@@ -1 +1 @@\n-old\n+new\n"
)


def _sym(file: str, symbol: str = "foo") -> ChangedSymbol:
    return ChangedSymbol(file=file, symbol=symbol, symbol_type="function", start_line=1, change_type="modified")


def _item(file: str, symbol: str, risk: str = "high", confidence: str = "medium") -> BlastRadiusItem:
    return BlastRadiusItem(file=file, line=1, symbol=symbol, risk=risk, confidence=confidence, reason="test")


def _run_adversarial_pass(blast_items, diff, cfg, context_pack):
    """Mirrors the adversarial pass in luna.py."""
    if not (context_pack is not None and blast_items):
        return blast_items
    uncertain = [i for i in blast_items if i.risk == "high" and i.confidence != "high"]
    certain = [i for i in blast_items if not (i.risk == "high" and i.confidence != "high")]
    if uncertain:
        files = {i.file for i in uncertain}
        ctx = build_adversarial_context(diff, files, context_pack)
        uncertain = adversarial_verify(uncertain, ctx, cfg)
    return certain + uncertain


def test_adversarial_called_for_uncertain_high(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    cfg = Config()  # no domains needed
    sym = _sym("src/private/a.ts", "funcA")
    pack = build_context_pack([sym], [], related_rules=[], related_tests=[])
    items = [_item("src/private/a.ts", "funcA")]
    confirm = json.dumps([{"index": 0, "confirmed": True, "reason": "保留"}])

    with patch("phases.adversarial_verifier.call_claude", return_value=confirm) as mock_adv:
        _run_adversarial_pass(items, PRIVATE_DIFF, cfg, pack)

    mock_adv.assert_called_once()


def test_adversarial_not_called_for_high_confidence():
    cfg = Config()
    sym = _sym("src/a.ts", "foo")
    pack = build_context_pack([sym], [], related_rules=[], related_tests=[])
    items = [_item("src/a.ts", "foo", risk="high", confidence="high")]

    with patch("phases.adversarial_verifier.call_claude") as mock_adv:
        _run_adversarial_pass(items, "", cfg, pack)

    mock_adv.assert_not_called()


def test_adversarial_refuted_finding_removed(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    cfg = Config()
    sym = _sym("src/private/a.ts", "funcA")
    pack = build_context_pack([sym], [], related_rules=[], related_tests=[])
    items = [_item("src/private/a.ts", "funcA")]
    refute = json.dumps([{"index": 0, "confirmed": False, "reason": "调用方不使用返回值"}])

    with patch("phases.adversarial_verifier.call_claude", return_value=refute):
        result = _run_adversarial_pass(items, PRIVATE_DIFF, cfg, pack)

    assert result == []


def test_adversarial_error_keeps_original(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    cfg = Config()
    sym = _sym("src/private/a.ts", "funcA")
    pack = build_context_pack([sym], [], related_rules=[], related_tests=[])
    items = [_item("src/private/a.ts", "funcA")]

    with patch("phases.adversarial_verifier.call_claude", side_effect=RuntimeError("timeout")):
        result = _run_adversarial_pass(items, PRIVATE_DIFF, cfg, pack)

    assert len(result) == 1
```

- [ ] **Step 5：运行全量测试，预期全部通过**

```bash
cd /Users/wangyinlong/luna && python3 -m pytest --tb=short -q
```

Expected: 全部通过，无 ImportError

- [ ] **Step 6：确认 luna.py 里没有 domain_classifier、cfg.domains 残留**

```bash
cd /Users/wangyinlong/luna && grep -n "domain_classifier\|cfg\.domains\|DomainEntry" luna.py
```

Expected: 无输出

- [ ] **Step 7：commit**

```bash
cd /Users/wangyinlong/luna
git add luna.py tests/test_adversarial_wiring.py
git commit -m "fix: adversarial verify runs unconditionally, remove domain gating"
```

---

## Self-Review

**Spec coverage：**

| 需求 | Task |
|---|---|
| 删除 `max_parallel_domains` | Task 2 config.py |
| 删除 `DomainEntry` + `Config.domains` | Task 2 config.py |
| 删除 `classify_symbols_by_domain` + `group_findings_by_domain` | Task 2 删文件 |
| `filter_diff_for_files` 保留并移入 adversarial_verifier | Task 1 |
| `build_adversarial_context` 新签名 `(diff, finding_files, context_pack)` | Task 1 |
| adversarial verify 无条件运行（去掉 `cfg.domains` 门控） | Task 3 |
| phase_list 无条件加 adversarial | Task 3 |
| 测试文件删除/更新 | Task 1-3 |

**Type consistency：**
- Task 1 `build_adversarial_context(diff, finding_files, context_pack)` → Task 3 调用 `_build_ctx(diff, _files, context_pack)`，`_files = {i.file for i in _uncertain}` 是 `set[str]` ✓
- Task 3 wiring 中 `_uncertain + _certain` 均是 `list[BlastRadiusItem]` ✓
- Task 2 删掉 `DomainEntry` 后，Task 1 的 adversarial_verifier 不再 import 它 ✓

**Placeholder scan：** 无 TBD/TODO，所有步骤含完整代码。
