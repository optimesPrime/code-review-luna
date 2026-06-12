# Adversarial Verify 实现计划 ✅ 已完成（2026-06-12）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在现有单次 `blast_radius.analyze()` 之后，对"high risk + non-high confidence"的灰色发现做域级反驳验证，过滤误报，提升精准度，token 增量控制在 30-50%。

**Architecture:** 不改变 blast_radius 的单次调用逻辑。blast 结束后，按 `config.yaml` 里的 `domains` 配置把发现分到各业务域；对每个域内的不确定高风险发现额外发起一次 adversarial LLM 调用（用域级 diff + 调用方上下文反驳误报）；无域配置时零开销、行为与现在完全一致。

**Tech Stack:** Python 3.11+, `fnmatch`（文件路径匹配）, 现有 `call_claude()` / `blast_radius.analyze()`

---

## 文件结构

| 文件 | 动作 | 职责 |
|------|------|------|
| `config.py` | 修改 | 加 `DomainEntry` dataclass、`Config.domains`、`ReviewConfig.max_parallel_domains` |
| `phases/domain_classifier.py` | 新建 | 三个纯函数：符号分域、diff 过滤、发现分域 |
| `phases/adversarial_verifier.py` | 新建 | 构建反驳上下文 + adversarial LLM 调用 |
| `luna.py` | 修改 | blast 之后加 adversarial pass，phase_list 按需加 "adversarial" |
| `tests/test_config_domains.py` | 新建 | config 解析测试 |
| `tests/test_domain_classifier.py` | 新建 | 三个纯函数的单测 |
| `tests/test_adversarial_verifier.py` | 新建 | adversarial_verify + build_adversarial_context 单测 |
| `tests/test_adversarial_wiring.py` | 新建 | luna.py 接入的集成测试 |

---

## Task 1：DomainEntry + Config

**Files:**
- Modify: `config.py`
- Create: `tests/test_config_domains.py`

- [ ] **Step 1：在 `config.py` 加 `DomainEntry`、`Config.domains`、`ReviewConfig.max_parallel_domains`**

在 `ReviewConfig` 里加一个字段（紧跟 `apply_enabled`）：

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

在 `APIChangeConfig` 之后、`Config` 之前加：

```python
@dataclass
class DomainEntry:
    name: str
    patterns: list[str] = field(default_factory=list)
```

在 `Config` 里加 `domains` 字段（紧跟 `api_change`）：

```python
@dataclass
class Config:
    api: APIConfig = field(default_factory=APIConfig)
    review: ReviewConfig = field(default_factory=ReviewConfig)
    skills: list[SkillEntry] = field(default_factory=list)
    reports: ReportsConfig = field(default_factory=ReportsConfig)
    privacy: PrivacyConfig = field(default_factory=PrivacyConfig)
    backend: BackendConfig = field(default_factory=BackendConfig)
    migration: MigrationConfig = field(default_factory=MigrationConfig)
    api_change: APIChangeConfig = field(default_factory=APIChangeConfig)
    domains: list[DomainEntry] = field(default_factory=list)
```

在 `load_config()` 的 `return cfg` 之前加解析逻辑：

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

- [ ] **Step 2：写测试**

新建 `tests/test_config_domains.py`：

```python
import textwrap
from config import load_config


def test_load_config_parses_domains(tmp_path):
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text(textwrap.dedent("""\
        review:
          max_parallel_domains: 2
        domains:
          - name: "私募基金"
            patterns:
              - "src/private*"
          - name: "公募基金"
            patterns:
              - "src/public*"
    """))
    cfg = load_config(str(cfg_file))
    assert len(cfg.domains) == 2
    assert cfg.domains[0].name == "私募基金"
    assert "src/private*" in cfg.domains[0].patterns
    assert cfg.review.max_parallel_domains == 2


def test_load_config_empty_domains_by_default():
    cfg = load_config("nonexistent.yaml")
    assert cfg.domains == []
    assert cfg.review.max_parallel_domains == 3
```

- [ ] **Step 3：运行测试，预期通过**

```bash
python3 -m pytest tests/test_config_domains.py -v
```

Expected: 2 passed

- [ ] **Step 4：commit**

```bash
git add config.py tests/test_config_domains.py
git commit -m "feat: add DomainEntry + Config.domains for adversarial verify"
```

---

## Task 2：domain_classifier.py

**Files:**
- Create: `phases/domain_classifier.py`
- Create: `tests/test_domain_classifier.py`

三个纯函数，无 LLM 调用：

| 函数 | 输入 | 输出 |
|------|------|------|
| `classify_symbols_by_domain` | symbols + domain_configs | `dict[domain_name, list[ChangedSymbol]]` |
| `filter_diff_for_files` | diff str + file set | 只含那些文件的 diff str |
| `group_findings_by_domain` | blast items + domain_map | `dict[domain_name, list[BlastRadiusItem]]` |

- [ ] **Step 1：写失败测试**

新建 `tests/test_domain_classifier.py`：

```python
import pytest
from phases.domain_classifier import (
    classify_symbols_by_domain,
    filter_diff_for_files,
    group_findings_by_domain,
)
from phases.symbol_locator import ChangedSymbol
from phases.blast_radius import BlastRadiusItem
from config import DomainEntry


def _sym(file: str, symbol: str = "foo") -> ChangedSymbol:
    return ChangedSymbol(
        file=file, symbol=symbol,
        symbol_type="function", start_line=1, change_type="modified",
    )


def _item(file: str, symbol: str = "foo", risk: str = "high", confidence: str = "medium") -> BlastRadiusItem:
    return BlastRadiusItem(file=file, line=1, symbol=symbol, risk=risk, confidence=confidence, reason="test")


def _domains():
    return [
        DomainEntry(name="私募", patterns=["src/private*", "*/private/*"]),
        DomainEntry(name="公募", patterns=["src/public*", "*/public/*"]),
    ]


# --- classify_symbols_by_domain ---

def test_classify_assigns_correct_domain():
    syms = [_sym("src/private/order.ts"), _sym("src/public/fund.ts")]
    result = classify_symbols_by_domain(syms, _domains())
    assert set(result.keys()) == {"私募", "公募"}
    assert result["私募"][0].file == "src/private/order.ts"


def test_classify_unmatched_goes_to_fallback():
    syms = [_sym("src/shared/utils.ts")]
    result = classify_symbols_by_domain(syms, _domains())
    assert "_unclassified" in result
    assert result["_unclassified"][0].file == "src/shared/utils.ts"


def test_classify_no_domains_all_unclassified():
    syms = [_sym("src/foo.ts")]
    result = classify_symbols_by_domain(syms, [])
    assert result == {"_unclassified": syms}


def test_classify_matches_first_domain_only():
    domains = [
        DomainEntry(name="A", patterns=["src/ab*"]),
        DomainEntry(name="B", patterns=["src/a*"]),
    ]
    result = classify_symbols_by_domain([_sym("src/abc.ts")], domains)
    assert "A" in result and "B" not in result


def test_classify_empty_returns_empty():
    assert classify_symbols_by_domain([], _domains()) == {}


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


# --- group_findings_by_domain ---

def test_group_findings_maps_to_correct_domain():
    syms = [_sym("src/private/a.ts", "funcA"), _sym("src/public/b.ts", "funcB")]
    domain_map = classify_symbols_by_domain(syms, _domains())
    items = [_item("src/private/a.ts", "funcA"), _item("src/public/b.ts", "funcB")]
    result = group_findings_by_domain(items, domain_map)
    assert result["私募"][0].symbol == "funcA"
    assert result["公募"][0].symbol == "funcB"


def test_group_findings_unmatched_file_to_unclassified():
    domain_map = {"私募": [_sym("src/private/a.ts", "funcA")]}
    items = [_item("src/other/x.ts", "funcX")]
    result = group_findings_by_domain(items, domain_map)
    assert "_unclassified" in result
    assert result["_unclassified"][0].symbol == "funcX"


def test_group_findings_empty_items_returns_empty():
    domain_map = {"私募": [_sym("src/private/a.ts")]}
    assert group_findings_by_domain([], domain_map) == {}
```

- [ ] **Step 2：运行测试，预期失败**

```bash
python3 -m pytest tests/test_domain_classifier.py -v
```

Expected: ImportError（模块不存在）

- [ ] **Step 3：实现 `phases/domain_classifier.py`**

新建 `phases/domain_classifier.py`：

```python
from __future__ import annotations
import fnmatch
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from phases.symbol_locator import ChangedSymbol
    from phases.blast_radius import BlastRadiusItem
    from config import DomainEntry

_FALLBACK = "_unclassified"


def classify_symbols_by_domain(
    symbols: list["ChangedSymbol"],
    domain_configs: list["DomainEntry"],
) -> dict[str, list["ChangedSymbol"]]:
    if not symbols:
        return {}
    result: dict[str, list["ChangedSymbol"]] = {}
    for sym in symbols:
        matched = next(
            (d.name for d in domain_configs if any(fnmatch.fnmatch(sym.file, p) for p in d.patterns)),
            None,
        )
        result.setdefault(matched or _FALLBACK, []).append(sym)
    return result


def filter_diff_for_files(diff: str, files: set[str]) -> str:
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
            active = any(
                diff_path == f or diff_path.endswith("/" + f) or f.endswith("/" + diff_path)
                for f in files
            )
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


def group_findings_by_domain(
    items: list["BlastRadiusItem"],
    domain_map: dict[str, list["ChangedSymbol"]],
) -> dict[str, list["BlastRadiusItem"]]:
    if not items:
        return {}
    file_to_domain = {
        sym.file: domain_name
        for domain_name, syms in domain_map.items()
        for sym in syms
    }
    result: dict[str, list["BlastRadiusItem"]] = {}
    for item in items:
        domain = file_to_domain.get(item.file, _FALLBACK)
        result.setdefault(domain, []).append(item)
    return result
```

- [ ] **Step 4：运行测试，预期全部通过**

```bash
python3 -m pytest tests/test_domain_classifier.py -v
```

Expected: 11 passed

- [ ] **Step 5：commit**

```bash
git add phases/domain_classifier.py tests/test_domain_classifier.py
git commit -m "feat: domain_classifier — classify symbols, filter diff, group findings"
```

---

## Task 3：adversarial_verifier.py

**Files:**
- Create: `phases/adversarial_verifier.py`
- Create: `tests/test_adversarial_verifier.py`

两个函数：

| 函数 | 职责 |
|------|------|
| `build_adversarial_context` | 用域符号 + diff + caller_contexts 构建反驳用的 context 字符串 |
| `adversarial_verify` | 发起一次 LLM 调用尝试反驳 uncertain-high 发现，返回过滤后列表 |

- [ ] **Step 1：写失败测试**

新建 `tests/test_adversarial_verifier.py`：

```python
import json
from unittest.mock import patch
from phases.blast_radius import BlastRadiusItem
from phases.context_pack import ContextPack, build_context_pack
from phases.symbol_locator import ChangedSymbol
from phases.caller_context import CallerSnippet, SymbolCallers
from phases.adversarial_verifier import adversarial_verify, build_adversarial_context
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


def test_invalid_llm_response_keeps_all():
    finding = _item()
    with _mock_llm("not valid json"):
        result = adversarial_verify([finding], context_snippet="", config=None)
    assert len(result) == 1


def test_empty_input_returns_empty():
    assert adversarial_verify([], context_snippet="", config=None) == []


def test_prompt_contains_domain_context():
    finding = _item()
    calls = []
    def fake_call(system, user, config):
        calls.append(user)
        return json.dumps([{"index": 0, "confirmed": True, "reason": "保留"}])
    with patch("phases.adversarial_verifier.call_claude", side_effect=fake_call):
        adversarial_verify([finding], context_snippet="domain=私募\ncaller: pay(amount)", config=None)
    assert "domain=私募" in calls[0]
    assert "caller: pay(amount)" in calls[0]


# --- build_adversarial_context ---

def test_build_context_contains_domain_name():
    sym = _sym()
    pack = _pack_with_callers(sym, snippet="foo(x)")
    ctx = build_adversarial_context("私募", "diff content", [sym], pack)
    assert "domain=私募" in ctx


def test_build_context_contains_caller_snippet():
    sym = _sym()
    pack = _pack_with_callers(sym, snippet="pay(amount)")
    ctx = build_adversarial_context("私募", "diff content", [sym], pack)
    assert "pay(amount)" in ctx


def test_build_context_contains_filtered_diff():
    sym = _sym(file="src/a.ts")
    pack = build_context_pack([sym], [], related_rules=[], related_tests=[])
    diff = (
        "diff --git a/src/a.ts b/src/a.ts\n@@ -1 +1 @@\n-old\n+new\n"
        "diff --git a/src/b.ts b/src/b.ts\n@@ -1 +1 @@\n-old\n+new\n"
    )
    ctx = build_adversarial_context("私募", diff, [sym], pack)
    assert "src/a.ts" in ctx
    assert "src/b.ts" not in ctx


def test_build_context_excludes_other_domain_callers():
    sym_a = _sym(file="src/a.ts", symbol="funcA")
    sym_b = _sym(file="src/b.ts", symbol="funcB")
    pack = build_context_pack([sym_a, sym_b], [], related_rules=[], related_tests=[])
    pack.caller_contexts = [
        SymbolCallers(symbol="funcA", callers=[
            CallerSnippet(file="src/x.ts", line=1, snippet="funcA()", language="typescript")
        ], total_count=1),
        SymbolCallers(symbol="funcB", callers=[], total_count=0),
    ]
    ctx = build_adversarial_context("私募", "", [sym_a], pack)
    assert "funcA" in ctx
    assert "funcB" not in ctx
```

- [ ] **Step 2：运行测试，预期失败**

```bash
python3 -m pytest tests/test_adversarial_verifier.py -v
```

Expected: ImportError

- [ ] **Step 3：实现 `phases/adversarial_verifier.py`**

新建 `phases/adversarial_verifier.py`：

```python
from __future__ import annotations
import json
import re
from typing import TYPE_CHECKING

from api_client import call_claude

if TYPE_CHECKING:
    from phases.blast_radius import BlastRadiusItem
    from phases.context_pack import ContextPack
    from phases.symbol_locator import ChangedSymbol
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


def build_adversarial_context(
    domain_name: str,
    diff: str,
    domain_syms: list["ChangedSymbol"],
    context_pack: "ContextPack",
) -> str:
    from phases.domain_classifier import filter_diff_for_files
    domain_files = {s.file for s in domain_syms}
    domain_sym_names = {s.symbol for s in domain_syms}
    domain_diff = filter_diff_for_files(diff, domain_files)

    caller_lines: list[str] = []
    for sc in context_pack.caller_contexts:
        if sc.symbol in domain_sym_names:
            caller_lines.append(f"symbol={sc.symbol}; callers={sc.total_count}")
            for c in sc.callers[:3]:
                caller_lines.append(f"  {c.file}:{c.line}  {c.snippet}")

    callers_text = "\n".join(caller_lines[:30]) if caller_lines else "（无）"
    return (
        f"domain={domain_name}\n\n"
        f"## 调用方上下文\n{callers_text}\n\n"
        f"## domain-scoped diff\n```diff\n{domain_diff[:4000]}\n```"
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

- [ ] **Step 4：运行测试，预期全部通过**

```bash
python3 -m pytest tests/test_adversarial_verifier.py -v
```

Expected: 11 passed

- [ ] **Step 5：commit**

```bash
git add phases/adversarial_verifier.py tests/test_adversarial_verifier.py
git commit -m "feat: adversarial_verifier challenges uncertain-high findings"
```

---

## Task 4：接入 luna.py

**Files:**
- Modify: `luna.py`
- Create: `tests/test_adversarial_wiring.py`

在 blast 之后插入 adversarial pass。有域配置时执行，无配置时零开销。

- [ ] **Step 1：找到 phase_list 构建和 blast 调用位置**

```bash
grep -n "_phase_list\|blast\.analyze\|_finish.*blast" luna.py
```

应看到：
- `_phase_list += [... ("blast", "爆炸范围分析")]` 在约 181 行
- `blast_items, _blast_savings = blast.analyze(...)` 在约 371 行
- `_finish("blast")` 在约 377 行

- [ ] **Step 2：在 phase_list 构建处追加 adversarial 条目**

找到这一行：

```python
    if cfg.api_change.enabled:
        _phase_list += [("api_change", "API 契约检查")]
```

在它之后追加：

```python
    if cfg.domains:
        _phase_list += [("adversarial", "反驳验证")]
```

- [ ] **Step 3：在 `_finish("blast")` 之后插入 adversarial pass**

找到这段（约 377-379 行）：

```python
        _finish("blast")
        report.blast_radius_items = blast_items
        report.token_savings["blast"] = _blast_savings
```

替换为：

```python
        _finish("blast")

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

        report.blast_radius_items = blast_items
        report.token_savings["blast"] = _blast_savings
```

- [ ] **Step 4：写接入测试**

新建 `tests/test_adversarial_wiring.py`：

```python
import json
from unittest.mock import patch, MagicMock
from config import Config, DomainEntry
from phases.blast_radius import BlastRadiusItem
from phases.context_pack import build_context_pack
from phases.symbol_locator import ChangedSymbol


def _sym(file: str, symbol: str = "foo") -> ChangedSymbol:
    return ChangedSymbol(file=file, symbol=symbol, symbol_type="function", start_line=1, change_type="modified")


def _item(file: str, symbol: str, risk: str = "high", confidence: str = "medium") -> BlastRadiusItem:
    return BlastRadiusItem(file=file, line=1, symbol=symbol, risk=risk, confidence=confidence, reason="test")


def test_adversarial_verify_called_when_domains_configured(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    cfg = Config()
    cfg.domains = [DomainEntry(name="私募", patterns=["src/private*"])]

    sym = _sym("src/private/a.ts", "funcA")
    pack = build_context_pack([sym], [], related_rules=[], related_tests=[])

    blast_result = [_item("src/private/a.ts", "funcA", risk="high", confidence="medium")]
    confirm_resp = json.dumps([{"index": 0, "confirmed": True, "reason": "保留"}])

    with patch("phases.blast_radius.call_claude", return_value=json.dumps([
        {"file": "src/private/a.ts", "line": 1, "symbol": "funcA", "risk": "high", "confidence": "medium", "reason": "test"}
    ])):
        with patch("phases.adversarial_verifier.call_claude", return_value=confirm_resp) as mock_adv:
            from luna import run_review
            run_review(
                diff="diff --git a/src/private/a.ts b/src/private/a.ts\n@@ -1 +1 @@\n-old\n+new\n",
                config=cfg,
                quiet=True,
            )
    mock_adv.assert_called_once()


def test_adversarial_not_called_when_no_domains(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    cfg = Config()  # no domains

    with patch("phases.blast_radius.call_claude", return_value="[]"):
        with patch("phases.adversarial_verifier.call_claude") as mock_adv:
            from luna import run_review
            run_review(diff="diff --git a/a.ts b/a.ts\n@@ -1 +1 @@\n", config=cfg, quiet=True)
    mock_adv.assert_not_called()


def test_adversarial_refuted_finding_removed(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    cfg = Config()
    cfg.domains = [DomainEntry(name="私募", patterns=["src/private*"])]

    sym = _sym("src/private/a.ts", "funcA")
    pack = build_context_pack([sym], [], related_rules=[], related_tests=[])
    refute_resp = json.dumps([{"index": 0, "confirmed": False, "reason": "调用方不使用返回值"}])

    with patch("phases.blast_radius.call_claude", return_value=json.dumps([
        {"file": "src/private/a.ts", "line": 1, "symbol": "funcA", "risk": "high", "confidence": "medium", "reason": "test"}
    ])):
        with patch("phases.adversarial_verifier.call_claude", return_value=refute_resp):
            from luna import run_review
            report = run_review(
                diff="diff --git a/src/private/a.ts b/src/private/a.ts\n@@ -1 +1 @@\n-old\n+new\n",
                config=cfg,
                quiet=True,
            )
    assert report.blast_radius_items == []


def test_adversarial_error_keeps_original_findings(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    cfg = Config()
    cfg.domains = [DomainEntry(name="私募", patterns=["src/private*"])]

    with patch("phases.blast_radius.call_claude", return_value=json.dumps([
        {"file": "src/private/a.ts", "line": 1, "symbol": "funcA", "risk": "high", "confidence": "medium", "reason": "test"}
    ])):
        with patch("phases.adversarial_verifier.call_claude", side_effect=RuntimeError("timeout")):
            from luna import run_review
            report = run_review(
                diff="diff --git a/src/private/a.ts b/src/private/a.ts\n@@ -1 +1 @@\n-old\n+new\n",
                config=cfg,
                quiet=True,
            )
    assert len(report.blast_radius_items) == 1
```

- [ ] **Step 5：运行接入测试**

```bash
python3 -m pytest tests/test_adversarial_wiring.py -v
```

Expected: 4 passed

- [ ] **Step 6：全量回归**

```bash
python3 -m pytest --tb=short -q
```

Expected: 全部通过

- [ ] **Step 7：commit**

```bash
git add luna.py tests/test_adversarial_wiring.py
git commit -m "feat: wire adversarial verify into blast pipeline, fallback-safe"
```

---

## 使用方式

在项目 `config.yaml` 里加：

```yaml
review:
  max_parallel_domains: 3   # 并发上限，预留给未来扩展

domains:
  - name: "私募基金"
    patterns:
      - "src/private-fund/*"
      - "*/PrivateFund*"
  - name: "公募基金"
    patterns:
      - "src/public-fund/*"
      - "*/PublicFund*"
```

不加 `domains` 段，Luna 行为与现在完全一致，零开销。

---

## Self-Review

**Spec coverage：**

| 需求 | Task |
|------|------|
| 单次 blast 不变 | 不碰 blast_radius.analyze() |
| 按域分组发现 | Task 2 group_findings_by_domain |
| 域级 context 反驳（diff + caller_contexts） | Task 3 build_adversarial_context |
| 只对 uncertain-high 触发 | Task 3 adversarial_verify 过滤逻辑 + Task 4 |
| 配置驱动，无配置零开销 | Task 4 `if cfg.domains` 条件分支 |
| LLM 出错保守处理（保留原发现）| Task 3 except → return list(items)；Task 4 except → skip |
| domain 配置可扩展 | Task 1 DomainEntry + patterns |
| token 增量可控 | adversarial prompt 只含 uncertain-high findings + 域级 context，远小于完整 blast |

**Type consistency：**
- `classify_symbols_by_domain` 返回 `dict[str, list[ChangedSymbol]]` → Task 4 `_domain_map.get(_dname, [])` 传入 `build_adversarial_context` ✓
- `group_findings_by_domain` 返回 `dict[str, list[BlastRadiusItem]]` → Task 4 遍历 ✓
- `adversarial_verify(items, context_snippet, config)` — Task 3 定义，Task 4 调用一致 ✓
- `build_adversarial_context(domain_name, diff, domain_syms, context_pack)` — Task 3 定义，Task 4 传 `_domain_map.get(_dname, [])` ✓

**Placeholder scan：** 无 TBD / TODO。所有步骤含完整代码。
