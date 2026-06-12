# Multi-Domain Parallel Review 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将前端 `blast_radius` 阶段从单次 LLM 审查升级为"按业务域分块 → 并行多 agent 审查 → adversarial 反驳验证 → 合并去重 → 输出域审查摘要"的流程，大幅降低误报、提高精准度。

**Scope boundary:** 本计划只改前端 `blast_radius` 分析路径，不改变 `code_quality`、`backend_review`、`migration`、`api_change` 的审查逻辑。其他阶段继续按现有顺序执行。

**Architecture:** 新增 `phases/domain_classifier.py`（域分类）和 `phases/multi_domain_review.py`（并行编排 + adversarial 验证 + 域审查摘要）两个模块，复用现有 `blast_radius.analyze()` 纯函数作为每个域的审查单元。`luna.py` 在检测到域配置时切换前端 blast 到新路径，否则 fallback 到现有流程，零破坏性。

**Tech Stack:** Python 3.11+, `concurrent.futures.ThreadPoolExecutor`（并行 LLM 调用）, `fnmatch`（文件路径匹配）, 现有 `call_claude()` / `blast_radius.analyze()`

---

## 文件结构

| 文件 | 动作 | 职责 |
|------|------|------|
| `phases/domain_classifier.py` | 新建 | 按 fnmatch 模式把 ChangedSymbol 列表分组到各业务域 |
| `phases/multi_domain_review.py` | 新建 | 并行调用 blast_radius.analyze() + adversarial 验证 + 合并去重 + 域审查摘要 |
| `config.py` | 修改 | 新增 `DomainEntry` dataclass、`Config.domains` 字段、`review.max_parallel_domains` |
| `luna.py` | 修改 | 有域配置时仅前端 blast 走 `multi_domain_review`，无配置 fallback 原流程 |
| `tests/test_domain_classifier.py` | 新建 | 分类逻辑单测 |
| `tests/test_multi_domain_review.py` | 新建 | 编排逻辑单测（monkeypatch LLM） |

---

## Task 1：DomainEntry + 并发配置 — 在 config.py 里加域配置数据结构

**Files:**
- Modify: `config.py`

用户在 `config.yaml` 的 `domains` 段定义业务域，每个域有名字和文件路径模式列表（支持 `fnmatch` 通配符）。

```yaml
# config.yaml 示例
domains:
  - name: "私募基金"
    patterns:
      - "src/private*"
      - "*/private-fund/*"
  - name: "公募基金"
    patterns:
      - "src/public*"
      - "*/public-fund/*"
```

- [ ] **Step 1：在 `config.py` 加 `DomainEntry`、`Config.domains` 和并发上限**

在 `ReviewConfig` 里加并发上限，默认 3，避免 LLM provider rate limit：

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

在 `BackendConfig` 之后加：

```python
@dataclass
class DomainEntry:
    name: str
    patterns: list[str] = field(default_factory=list)
```

在 `Config` dataclass 里加一个字段：

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
    domains: list[DomainEntry] = field(default_factory=list)   # ← 新增
```

在 `load_config()` 函数末尾，`return cfg` 之前加解析逻辑：

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

- [ ] **Step 2：写测试验证 config 能正确解析 domains**

新建 `tests/test_config_domains.py`：

```python
import textwrap, tempfile, os
from config import load_config

def test_load_config_parses_domains(tmp_path):
    yaml_content = textwrap.dedent("""\
        review:
          max_parallel_domains: 2
        domains:
          - name: "私募基金"
            patterns:
              - "src/private*"
          - name: "公募基金"
            patterns:
              - "src/public*"
    """)
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text(yaml_content)
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
git commit -m "feat: add DomainEntry config for multi-domain review"
```

---

## Task 2：域分类器 — `phases/domain_classifier.py`

**Files:**
- Create: `phases/domain_classifier.py`
- Test: `tests/test_domain_classifier.py`

职责：把一组 `ChangedSymbol` 按域配置分组，未匹配的归入 `_unclassified` 兜底域。

- [ ] **Step 1：写失败测试**

新建 `tests/test_domain_classifier.py`：

```python
from phases.domain_classifier import classify_symbols_by_domain
from phases.symbol_locator import ChangedSymbol
from config import DomainEntry


def _sym(file: str, symbol: str = "foo") -> ChangedSymbol:
    return ChangedSymbol(
        file=file, symbol=symbol,
        symbol_type="function", start_line=1, change_type="modified",
    )


def _domains():
    return [
        DomainEntry(name="私募", patterns=["src/private*", "*/private/*"]),
        DomainEntry(name="公募", patterns=["src/public*", "*/public/*"]),
    ]


def test_classifies_by_file_pattern():
    syms = [
        _sym("src/private/order.ts"),
        _sym("src/public/fund.ts"),
    ]
    result = classify_symbols_by_domain(syms, _domains())
    assert set(result.keys()) == {"私募", "公募"}
    assert result["私募"][0].file == "src/private/order.ts"
    assert result["公募"][0].file == "src/public/fund.ts"


def test_unmatched_symbols_go_to_fallback():
    syms = [_sym("src/shared/utils.ts")]
    result = classify_symbols_by_domain(syms, _domains())
    assert "_unclassified" in result
    assert result["_unclassified"][0].file == "src/shared/utils.ts"


def test_no_domains_config_all_unclassified():
    syms = [_sym("src/foo.ts")]
    result = classify_symbols_by_domain(syms, [])
    assert result == {"_unclassified": syms}


def test_symbol_matches_first_domain_only():
    # 文件同时匹配两个 pattern，只归入第一个
    domains = [
        DomainEntry(name="A", patterns=["src/ab*"]),
        DomainEntry(name="B", patterns=["src/a*"]),
    ]
    syms = [_sym("src/abc.ts")]
    result = classify_symbols_by_domain(syms, domains)
    assert "A" in result
    assert "B" not in result


def test_same_domain_multiple_symbols():
    syms = [_sym("src/private/a.ts"), _sym("src/private/b.ts")]
    result = classify_symbols_by_domain(syms, _domains())
    assert len(result["私募"]) == 2


def test_empty_symbols_returns_empty():
    result = classify_symbols_by_domain([], _domains())
    assert result == {}
```

- [ ] **Step 2：运行测试，预期全部失败**

```bash
python3 -m pytest tests/test_domain_classifier.py -v
```

Expected: ImportError（模块不存在）

- [ ] **Step 3：实现 `phases/domain_classifier.py`**

```python
from __future__ import annotations
import fnmatch
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from phases.symbol_locator import ChangedSymbol
    from config import DomainEntry

_FALLBACK = "_unclassified"


def classify_symbols_by_domain(
    symbols: list["ChangedSymbol"],
    domain_configs: list["DomainEntry"],
) -> dict[str, list["ChangedSymbol"]]:
    """Map each symbol to its business domain using fnmatch patterns.

    Returns a dict keyed by domain name. Unmatched symbols go to '_unclassified'.
    Empty symbols list returns {}.
    """
    if not symbols:
        return {}

    result: dict[str, list["ChangedSymbol"]] = {}

    for sym in symbols:
        matched: str | None = None
        for domain in domain_configs:
            if any(fnmatch.fnmatch(sym.file, pat) for pat in domain.patterns):
                matched = domain.name
                break
        key = matched if matched is not None else _FALLBACK
        result.setdefault(key, []).append(sym)

    return result


def filter_diff_for_files(diff: str, files: set[str]) -> str:
    """Return only the diff hunks whose b/ path is in `files`.

    Returns an empty string when nothing matches. Multi-domain review must not
    leak the full diff into one domain because of path-format mismatch.
    """
    if not files:
        return ""

    result: list[str] = []
    lines = diff.split("\n")
    n = len(lines)
    i = 0
    file_header: list[str] = []
    active = False

    while i < n:
        line = lines[i]
        if line.startswith("diff --git "):
            parts = line.split(" b/", 1)
            diff_path = parts[1].strip() if len(parts) == 2 else ""
            active = any(diff_path == f or diff_path.endswith("/" + f) or f.endswith("/" + diff_path) for f in files)
            file_header = [line]
            i += 1
            while i < n and not lines[i].startswith("@@") and not lines[i].startswith("diff --git "):
                file_header.append(lines[i])
                i += 1
            continue
        if line.startswith("@@") or not line.startswith("diff --git "):
            if active:
                if file_header:
                    result.extend(file_header)
                    file_header = []
                result.append(line)
            i += 1
            continue
        i += 1

    return "\n".join(result)
```

- [ ] **Step 4：补充 diff 过滤防泄漏测试**

在 `tests/test_domain_classifier.py` 追加：

```python
def test_filter_diff_for_files_returns_empty_when_no_match():
    from phases.domain_classifier import filter_diff_for_files
    diff = "diff --git a/src/a.ts b/src/a.ts\n@@ -1 +1 @@\n-old\n+new\n"
    assert filter_diff_for_files(diff, {"src/b.ts"}) == ""


def test_filter_diff_for_files_keeps_only_matching_file():
    from phases.domain_classifier import filter_diff_for_files
    diff = (
        "diff --git a/src/a.ts b/src/a.ts\n@@ -1 +1 @@\n-old\n+new\n"
        "diff --git a/src/b.ts b/src/b.ts\n@@ -1 +1 @@\n-old\n+new\n"
    )
    filtered = filter_diff_for_files(diff, {"src/b.ts"})
    assert "b/src/b.ts" in filtered
    assert "b/src/a.ts" not in filtered
```

- [ ] **Step 5：运行测试，预期全部通过**

```bash
python3 -m pytest tests/test_domain_classifier.py -v
```

Expected: 8 passed

- [ ] **Step 6：commit**

```bash
git add phases/domain_classifier.py tests/test_domain_classifier.py
git commit -m "feat: domain classifier splits symbols by file pattern"
```

---

## Task 3：Context Pack 过滤器

**Files:**
- Modify: `phases/domain_classifier.py`（追加一个函数）
- Modify: `tests/test_domain_classifier.py`（追加测试）

把一个完整的 `ContextPack` 按域符号列表裁剪成小版，只保留与该域相关的 impact_paths / caller_contexts / file_history。注意：`review_focus` 和 `review_questions` 默认是全局信号，过滤后的 pack 需要重写成 domain-scoped 提示，避免把其他域的问题带进 prompt。

- [ ] **Step 1：写失败测试**

在 `tests/test_domain_classifier.py` 末尾追加：

```python
from phases.context_pack import build_context_pack
from phases.risk_propagation import ImpactPath
from phases.caller_context import CallerSnippet, SymbolCallers


def _impact(path_head: str, risk: str = "high") -> ImpactPath:
    return ImpactPath(
        path=[path_head, "src/other.ts"],
        risk=risk, confidence="high",
        evidence="test evidence",
    )


def test_filter_context_pack_keeps_only_domain_symbols():
    syms_all = [_sym("src/private/a.ts", "funcA"), _sym("src/public/b.ts", "funcB")]
    domain_syms = [_sym("src/private/a.ts", "funcA")]
    pack = build_context_pack(syms_all, [], related_rules=[], related_tests=[])

    from phases.domain_classifier import filter_context_pack_for_domain
    filtered = filter_context_pack_for_domain(pack, domain_syms)
    assert len(filtered.changed_symbols) == 1
    assert filtered.changed_symbols[0].symbol == "funcA"


def test_filter_context_pack_keeps_relevant_impact_paths():
    private_sym = _sym("src/private/a.ts", "funcA")
    public_sym = _sym("src/public/b.ts", "funcB")
    paths = [
        _impact("src/private/a.ts:funcA", "high"),
        _impact("src/public/b.ts:funcB", "medium"),
    ]
    pack = build_context_pack([private_sym, public_sym], paths, related_rules=[], related_tests=[])

    from phases.domain_classifier import filter_context_pack_for_domain
    filtered = filter_context_pack_for_domain(pack, [private_sym])
    assert all("private" in str(p.path[0]) for p in filtered.impact_paths)


def test_filter_context_pack_keeps_path_when_domain_file_is_not_head():
    private_sym = _sym("src/private/a.ts", "funcA")
    paths = [
        ImpactPath(
            path=["src/shared.ts:helper", "src/private/a.ts:funcA"],
            risk="high", confidence="high", evidence="test evidence",
        )
    ]
    pack = build_context_pack([private_sym], paths, related_rules=[], related_tests=[])

    from phases.domain_classifier import filter_context_pack_for_domain
    filtered = filter_context_pack_for_domain(pack, [private_sym])
    assert len(filtered.impact_paths) == 1


def test_filter_context_pack_keeps_relevant_caller_contexts():
    private_sym = _sym("src/private/a.ts", "funcA")
    public_sym = _sym("src/public/b.ts", "funcB")
    pack = build_context_pack([private_sym, public_sym], [], related_rules=[], related_tests=[])
    pack.caller_contexts = [
        SymbolCallers(symbol="funcA", callers=[
            CallerSnippet(file="src/x.ts", line=1, snippet="funcA()", language="typescript")
        ], total_count=1),
        SymbolCallers(symbol="funcB", callers=[], total_count=0),
    ]

    from phases.domain_classifier import filter_context_pack_for_domain
    filtered = filter_context_pack_for_domain(pack, [private_sym])
    assert all(sc.symbol == "funcA" for sc in filtered.caller_contexts)
```

- [ ] **Step 2：运行测试，预期失败**

```bash
python3 -m pytest tests/test_domain_classifier.py::test_filter_context_pack_keeps_only_domain_symbols -v
```

Expected: ImportError（`filter_context_pack_for_domain` 不存在）

- [ ] **Step 3：在 `phases/domain_classifier.py` 末尾追加**

```python
from phases.context_pack import ContextPack
from phases.symbol_locator import ChangedSymbol


def filter_context_pack_for_domain(
    pack: ContextPack,
    domain_symbols: list[ChangedSymbol],
) -> ContextPack:
    """Return a shallow copy of pack filtered to only domain_symbols."""
    domain_files = {s.file for s in domain_symbols}
    domain_sym_names = {s.symbol for s in domain_symbols}

    def _path_hits_domain(path: list[str]) -> bool:
        return any(
            any(node == f or node.startswith(f + ":") or f in node for f in domain_files)
            for node in path
        )

    filtered_paths = [
        p for p in pack.impact_paths
        if p.path and _path_hits_domain(p.path)
    ]
    filtered_callers = [
        sc for sc in pack.caller_contexts
        if sc.symbol in domain_sym_names
    ]
    filtered_history = {
        f: v for f, v in pack.file_history.items() if f in domain_files
    }

    domain_focus = [
        f"当前只审查业务域内符号：{', '.join(sorted(domain_sym_names))}"
    ]

    new_pack = ContextPack(
        changed_symbols=domain_symbols,
        impact_paths=filtered_paths,
        related_rules=pack.related_rules,
        related_tests=pack.related_tests,
        review_focus=domain_focus,
        review_questions=[],
        file_history=filtered_history,
        caller_contexts=filtered_callers,
    )
    return new_pack
```

- [ ] **Step 4：运行测试，预期全部通过**

```bash
python3 -m pytest tests/test_domain_classifier.py -v
```

Expected: 12 passed（Task 2 的 8 个 + 本任务新增 4 个）

- [ ] **Step 5：commit**

```bash
git add phases/domain_classifier.py tests/test_domain_classifier.py
git commit -m "feat: filter_context_pack_for_domain slices pack by domain symbols"
```

---

## Task 4：Adversarial 验证器

**Files:**
- Create: `phases/adversarial_verifier.py`
- Test: `tests/test_adversarial_verifier.py`

对"high 风险但 confidence 非 high"的 finding 做批量反驳验证，用一次 LLM 调用过滤误报。验证上下文必须来自对应 domain 的 `ContextPack` 和 filtered diff，不能用全局 diff 截断。

- [ ] **Step 1：写失败测试**

新建 `tests/test_adversarial_verifier.py`：

```python
import json
from unittest.mock import patch
from phases.blast_radius import BlastRadiusItem
from phases.adversarial_verifier import adversarial_verify


def _item(risk="high", confidence="medium", symbol="foo", file="a.ts") -> BlastRadiusItem:
    return BlastRadiusItem(
        file=file, line=1, symbol=symbol,
        risk=risk, confidence=confidence,
        reason="可能影响支付流程",
    )


def _mock_call(response: str):
    return patch("phases.adversarial_verifier.call_claude", return_value=response)


def test_confirmed_finding_survives():
    finding = _item(risk="high", confidence="medium")
    response = json.dumps([{"index": 0, "confirmed": True, "reason": "确实影响支付"}])
    with _mock_call(response):
        result = adversarial_verify([finding], context_snippet="pay(amount)", config=None)
    assert len(result) == 1
    assert result[0].symbol == "foo"


def test_refuted_finding_is_removed():
    finding = _item(risk="high", confidence="medium")
    response = json.dumps([{"index": 0, "confirmed": False, "reason": "调用方不使用返回值"}])
    with _mock_call(response):
        result = adversarial_verify([finding], context_snippet="", config=None)
    assert result == []


def test_high_confidence_items_skip_verification():
    # confidence=high 的不需要反驳，直接保留
    finding = _item(risk="high", confidence="high")
    with patch("phases.adversarial_verifier.call_claude") as mock_llm:
        result = adversarial_verify([finding], context_snippet="", config=None)
    mock_llm.assert_not_called()
    assert len(result) == 1


def test_low_risk_items_skip_verification():
    finding = _item(risk="low", confidence="low")
    with patch("phases.adversarial_verifier.call_claude") as mock_llm:
        result = adversarial_verify([finding], context_snippet="", config=None)
    mock_llm.assert_not_called()
    assert len(result) == 1


def test_invalid_llm_response_keeps_all_items():
    # LLM 返回乱码时保守处理：全部保留
    finding = _item(risk="high", confidence="medium")
    with _mock_call("not valid json"):
        result = adversarial_verify([finding], context_snippet="", config=None)
    assert len(result) == 1


def test_prompt_contains_domain_caller_context():
    finding = _item(risk="high", confidence="medium")
    calls = []
    def fake_call(system, user, config):
        calls.append(user)
        return json.dumps([{"index": 0, "confirmed": True, "reason": "保留"}])

    with patch("phases.adversarial_verifier.call_claude", side_effect=fake_call):
        adversarial_verify(
            [finding],
            context_snippet="domain=私募\ncaller: pay(amount)\nfiltered diff",
            config=None,
        )
    assert "domain=私募" in calls[0]
    assert "caller: pay(amount)" in calls[0]


def test_empty_input_returns_empty():
    result = adversarial_verify([], context_snippet="", config=None)
    assert result == []
```

- [ ] **Step 2：运行测试，预期失败**

```bash
python3 -m pytest tests/test_adversarial_verifier.py -v
```

Expected: ImportError

- [ ] **Step 3：实现 `phases/adversarial_verifier.py`**

```python
from __future__ import annotations
import json
from typing import TYPE_CHECKING

from api_client import call_claude

if TYPE_CHECKING:
    from phases.blast_radius import BlastRadiusItem
    from config import Config

_SYSTEM = """\
你是代码审查质疑者。你将收到一批"high 风险但置信度非 high"的审查发现，以及相关代码上下文。
你的任务是：逐条尝试证明每个发现是误报（false positive）。

判断原则：
- 调用方代码中没有使用改动的属性或返回值 → 不是真实风险（confirmed: false）
- 改动的符号在项目内根本没有调用方 → 不是真实风险
- 风险理由与代码上下文明显不符 → 不是真实风险
- 找不到足够的反驳理由 → 保留（confirmed: true）

以 JSON 数组输出，每个元素包含：
- index: 原始 finding 的序号（整数）
- confirmed: 是否确认为真实风险（bool）
- reason: 你的判断理由（中文，一句话）

只输出 JSON 数组，不要其他内容。"""


def adversarial_verify(
    items: list["BlastRadiusItem"],
    context_snippet: str,
    config: "Config | None",
) -> list["BlastRadiusItem"]:
    """Filter items by adversarial LLM challenge.

    Only high-risk, non-high-confidence items are challenged.
    Others pass through unchanged. On LLM error, all items are kept (safe default).
    """
    if not items:
        return []

    # Separate: items that need verification vs items that pass through
    to_verify: list[tuple[int, "BlastRadiusItem"]] = [
        (i, item) for i, item in enumerate(items)
        if item.risk == "high" and item.confidence != "high"
    ]

    if not to_verify:
        return list(items)

    findings_text = json.dumps(
        [
            {
                "index": i,
                "file": item.file,
                "symbol": item.symbol,
                "reason": item.reason,
                "confidence": item.confidence,
            }
            for i, item in to_verify
        ],
        ensure_ascii=False,
        indent=2,
    )

    user = (
        f"## 待验证 findings\n\n```json\n{findings_text}\n```\n\n"
        f"## 相关代码上下文\n\n```\n{context_snippet[:3000]}\n```"
    )

    try:
        raw = call_claude(_SYSTEM, user, config)
        match = __import__("re").search(r"\[.*\]", raw, __import__("re").DOTALL)
        verdicts = json.loads(match.group()) if match else []
    except Exception:
        # LLM 出错时保守处理：全部保留
        return list(items)

    refuted_indices = {
        v["index"] for v in verdicts
        if isinstance(v, dict) and not v.get("confirmed", True)
    }

    result: list["BlastRadiusItem"] = []
    verify_indices = {i for i, _ in to_verify}

    for i, item in enumerate(items):
        if i in verify_indices and i in refuted_indices:
            continue  # 被反驳，丢弃
        result.append(item)

    return result
```

- [ ] **Step 4：运行测试，预期全部通过**

```bash
python3 -m pytest tests/test_adversarial_verifier.py -v
```

Expected: 7 passed

- [ ] **Step 5：commit**

```bash
git add phases/adversarial_verifier.py tests/test_adversarial_verifier.py
git commit -m "feat: adversarial_verifier challenges uncertain-high findings"
```

---

## Task 5：多域编排器 — `phases/multi_domain_review.py`

**Files:**
- Create: `phases/multi_domain_review.py`
- Test: `tests/test_multi_domain_review.py`

并行调用每个域的 `blast_radius.analyze()`，汇总结果，对不确定的高风险发现做 domain-scoped adversarial 验证，最后按 (file, line, symbol) 去重合并，并输出每个域的审查摘要。

- [ ] **Step 1：写失败测试**

新建 `tests/test_multi_domain_review.py`：

```python
import json
from unittest.mock import patch, MagicMock
from phases.blast_radius import BlastRadiusItem
from phases.context_pack import build_context_pack
from phases.symbol_locator import ChangedSymbol
from phases.multi_domain_review import multi_domain_review
from config import Config, DomainEntry


def _sym(file: str, symbol: str = "foo") -> ChangedSymbol:
    return ChangedSymbol(
        file=file, symbol=symbol,
        symbol_type="function", start_line=1, change_type="modified",
    )


def _item(file: str, symbol: str, risk: str = "high") -> BlastRadiusItem:
    return BlastRadiusItem(
        file=file, line=1, symbol=symbol,
        risk=risk, confidence="medium", reason="test",
    )


def _cfg_with_domains(*domain_specs):
    cfg = Config()
    cfg.domains = [DomainEntry(name=n, patterns=p) for n, p in domain_specs]
    return cfg


def test_two_domains_run_parallel_and_merge(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    syms = [_sym("src/private/a.ts", "funcA"), _sym("src/public/b.ts", "funcB")]
    pack = build_context_pack(syms, [], related_rules=[], related_tests=[])
    diff = (
        "diff --git a/src/private/a.ts b/src/private/a.ts\n@@ -1 +1 @@\n-old\n+new\n"
        "diff --git a/src/public/b.ts b/src/public/b.ts\n@@ -1 +1 @@\n-old\n+new\n"
    )
    cfg = _cfg_with_domains(
        ("私募", ["src/private*"]),
        ("公募", ["src/public*"]),
    )

    private_result = ([_item("src/private/a.ts", "funcA")], {})
    public_result = ([_item("src/public/b.ts", "funcB")], {})

    call_count = {"n": 0}
    def fake_analyze(diff, skill_context, config, context_pack, project_root, detail_level):
        call_count["n"] += 1
        if "funcA" in [s.symbol for s in context_pack.changed_symbols]:
            return private_result
        return public_result

    with patch("phases.multi_domain_review.blast_analyze", side_effect=fake_analyze):
        with patch("phases.multi_domain_review.adversarial_verify", side_effect=lambda items, **kw: items):
            items, savings = multi_domain_review(
                diff=diff, skill_context="", config=cfg,
                context_pack=pack, project_root=".", detail_level="standard",
            )

    assert call_count["n"] == 2
    symbols_found = {i.symbol for i in items}
    assert "funcA" in symbols_found
    assert "funcB" in symbols_found


def test_deduplicates_same_finding_from_multiple_domains(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    syms = [_sym("src/shared/c.ts", "funcC")]
    pack = build_context_pack(syms, [], related_rules=[], related_tests=[])
    diff = "diff --git a/src/shared/c.ts b/src/shared/c.ts\n@@ -1 +1 @@\n-old\n+new\n"
    cfg = _cfg_with_domains(
        ("A", ["src/a*"]),
        ("B", ["src/b*"]),
    )
    # funcC matches neither domain → goes to _unclassified once
    dup_item = _item("src/shared/c.ts", "funcC")
    with patch("phases.multi_domain_review.blast_analyze", return_value=([dup_item], {})):
        with patch("phases.multi_domain_review.adversarial_verify", side_effect=lambda items, **kw: items):
            items, _ = multi_domain_review(
                diff=diff, skill_context="", config=cfg,
                context_pack=pack, project_root=".", detail_level="standard",
            )
    # _unclassified runs once, so no duplicate
    assert sum(1 for i in items if i.symbol == "funcC") == 1


def test_fallback_to_single_domain_when_no_config(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    syms = [_sym("src/a.ts")]
    pack = build_context_pack(syms, [], related_rules=[], related_tests=[])
    cfg = Config()  # no domains

    single_result = ([_item("src/a.ts", "foo")], {})
    with patch("phases.multi_domain_review.blast_analyze", return_value=single_result):
        with patch("phases.multi_domain_review.adversarial_verify", side_effect=lambda items, **kw: items):
            items, _ = multi_domain_review(
                diff="", skill_context="", config=cfg,
                context_pack=pack, project_root=".", detail_level="standard",
            )
    assert len(items) == 1


def test_domain_failure_is_recorded_and_other_domains_continue(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    syms = [_sym("src/private/a.ts", "funcA"), _sym("src/public/b.ts", "funcB")]
    pack = build_context_pack(syms, [], related_rules=[], related_tests=[])
    diff = (
        "diff --git a/src/private/a.ts b/src/private/a.ts\n@@ -1 +1 @@\n-old\n+new\n"
        "diff --git a/src/public/b.ts b/src/public/b.ts\n@@ -1 +1 @@\n-old\n+new\n"
    )
    cfg = _cfg_with_domains(("私募", ["src/private*"]), ("公募", ["src/public*"]))

    def fake_analyze(diff, skill_context, config, context_pack, project_root, detail_level):
        if "funcA" in [s.symbol for s in context_pack.changed_symbols]:
            raise RuntimeError("LLM timeout")
        return [_item("src/public/b.ts", "funcB")], {"actual_tokens": 10}

    with patch("phases.multi_domain_review.blast_analyze", side_effect=fake_analyze):
        with patch("phases.multi_domain_review.adversarial_verify", side_effect=lambda items, **kw: items):
            items, meta = multi_domain_review(diff, "", cfg, pack, ".", "standard")

    assert [i.symbol for i in items] == ["funcB"]
    summaries = meta["domains"]
    assert any(s["domain"] == "私募" and s["status"] == "failed" for s in summaries)
    assert any(s["domain"] == "公募" and s["status"] == "ok" for s in summaries)


def test_all_domain_failures_fallback_to_single_blast(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    syms = [_sym("src/private/a.ts", "funcA")]
    pack = build_context_pack(syms, [], related_rules=[], related_tests=[])
    diff = "diff --git a/src/private/a.ts b/src/private/a.ts\n@@ -1 +1 @@\n-old\n+new\n"
    cfg = _cfg_with_domains(("私募", ["src/private*"]))
    fallback = [_item("src/private/a.ts", "funcA")]

    calls = {"n": 0}
    def fake_analyze(diff, skill_context, config, context_pack, project_root, detail_level):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("LLM timeout")
        return fallback, {"fallback": True}

    with patch("phases.multi_domain_review.blast_analyze", side_effect=fake_analyze):
        with patch("phases.multi_domain_review.adversarial_verify", side_effect=lambda items, **kw: items):
            items, meta = multi_domain_review(diff, "", cfg, pack, ".", "standard")

    assert items == fallback
    assert meta["fallback_used"] is True


def test_adversarial_receives_domain_scoped_context(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    syms = [_sym("src/private/a.ts", "funcA")]
    pack = build_context_pack(syms, [], related_rules=[], related_tests=[])
    cfg = _cfg_with_domains(("私募", ["src/private*"]))
    contexts = []

    with patch("phases.multi_domain_review.blast_analyze", return_value=([_item("src/private/a.ts", "funcA")], {})):
        with patch("phases.multi_domain_review.adversarial_verify", side_effect=lambda items, context_snippet, config: contexts.append(context_snippet) or items):
            multi_domain_review(
                "diff --git a/src/private/a.ts b/src/private/a.ts\n@@ -1 +1 @@\n-old\n+new\n",
                "", cfg, pack, ".", "standard",
            )

    assert "domain=私募" in contexts[0]
    assert "b/src/private/a.ts" in contexts[0]
```

- [ ] **Step 2：运行测试，预期失败**

```bash
python3 -m pytest tests/test_multi_domain_review.py -v
```

Expected: ImportError

- [ ] **Step 3：实现 `phases/multi_domain_review.py`**

```python
from __future__ import annotations
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING

from phases.blast_radius import analyze as blast_analyze, BlastRadiusItem
from phases.adversarial_verifier import adversarial_verify
from phases.domain_classifier import (
    classify_symbols_by_domain,
    filter_context_pack_for_domain,
    filter_diff_for_files,
)

if TYPE_CHECKING:
    from phases.context_pack import ContextPack
    from config import Config

_FALLBACK_DOMAIN = "_unclassified"


@dataclass
class DomainReviewSummary:
    domain: str
    symbol_count: int
    finding_count: int = 0
    status: str = "pending"  # "ok" | "failed" | "skipped"
    error: str = ""


def _build_adversarial_context(domain_name: str, domain_diff: str, domain_pack: "ContextPack") -> str:
    caller_lines: list[str] = []
    for sc in getattr(domain_pack, "caller_contexts", []):
        caller_lines.append(f"symbol={sc.symbol}; total_callers={sc.total_count}")
        for c in sc.callers[:3]:
            caller_lines.append(f"{c.file}:{c.line}\n{c.snippet}")

    return (
        f"domain={domain_name}\n\n"
        f"## caller_contexts\n" + "\n\n".join(caller_lines[:20]) + "\n\n"
        f"## filtered_diff\n{domain_diff[:3000]}"
    )


def multi_domain_review(
    diff: str,
    skill_context: str,
    config: "Config",
    context_pack: "ContextPack",
    project_root: str,
    detail_level: str,
) -> tuple[list[BlastRadiusItem], dict]:
    """Run blast radius review per business domain in parallel, then adversarially verify.

    Falls back to single-domain analysis when no domain config is defined.
    """
    domain_configs = config.domains if config else []
    symbols = context_pack.changed_symbols if context_pack else []

    if not domain_configs:
        return blast_analyze(diff, skill_context, config, context_pack, project_root, detail_level)

    # Classify symbols into domains
    domain_map = classify_symbols_by_domain(symbols, domain_configs)
    if not domain_map:
        return blast_analyze(diff, skill_context, config, context_pack, project_root, detail_level)

    # Build per-domain (diff, context_pack) pairs
    tasks: list[tuple[str, str, ContextPack]] = []
    summaries: dict[str, DomainReviewSummary] = {}
    for domain_name, domain_syms in domain_map.items():
        domain_files = {s.file for s in domain_syms}
        domain_diff = filter_diff_for_files(diff, domain_files)
        domain_pack = filter_context_pack_for_domain(context_pack, domain_syms)
        summaries[domain_name] = DomainReviewSummary(
            domain=domain_name,
            symbol_count=len(domain_syms),
            status="pending",
        )
        if not domain_diff.strip():
            summaries[domain_name].status = "skipped"
            summaries[domain_name].error = "no diff matched domain symbols"
            continue
        tasks.append((domain_name, domain_diff, domain_pack))

    if not tasks:
        items, savings = blast_analyze(diff, skill_context, config, context_pack, project_root, detail_level)
        return items, {
            "fallback_used": True,
            "fallback_reason": "no domain diff matched",
            "single": savings,
            "domains": [asdict(s) for s in summaries.values()],
        }

    # Parallel LLM calls
    all_items: list[BlastRadiusItem] = []
    combined_savings: dict = {"fallback_used": False, "domains": []}
    successful_domains = 0

    def _run_domain(name: str, d_diff: str, d_pack: "ContextPack"):
        items, savings = blast_analyze(d_diff, skill_context, config, d_pack, project_root, detail_level)
        context = _build_adversarial_context(name, d_diff, d_pack)
        verified = adversarial_verify(items, context_snippet=context, config=config)
        return verified, savings, context

    max_workers = max(1, min(len(tasks), getattr(config.review, "max_parallel_domains", 3)))
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(_run_domain, name, d_diff, d_pack): name
            for name, d_diff, d_pack in tasks
        }
        for future in as_completed(futures):
            domain_name = futures[future]
            try:
                items, savings, context = future.result()
                all_items.extend(items)
                combined_savings[domain_name] = savings
                summaries[domain_name].status = "ok"
                summaries[domain_name].finding_count = len(items)
                successful_domains += 1
            except Exception:
                summaries[domain_name].status = "failed"
                summaries[domain_name].error = "domain review failed"

    if successful_domains == 0:
        items, savings = blast_analyze(diff, skill_context, config, context_pack, project_root, detail_level)
        combined_savings.update({
            "fallback_used": True,
            "fallback_reason": "all domain reviews failed",
            "single": savings,
            "domains": [asdict(s) for s in summaries.values()],
        })
        return items, combined_savings

    # Deduplicate by (file, line, symbol)
    seen: set[tuple[str, int, str]] = set()
    deduped: list[BlastRadiusItem] = []
    for item in all_items:
        key = (item.file, item.line, item.symbol)
        if key not in seen:
            seen.add(key)
            deduped.append(item)

    combined_savings["domains"] = [asdict(s) for s in summaries.values()]
    return deduped, combined_savings
```

- [ ] **Step 4：运行测试，预期全部通过**

```bash
python3 -m pytest tests/test_multi_domain_review.py -v
```

Expected: 7 passed

- [ ] **Step 5：commit**

```bash
git add phases/multi_domain_review.py tests/test_multi_domain_review.py
git commit -m "feat: multi_domain_review parallel blast + adversarial verify"
```

---

## Task 6：接入 luna.py

**Files:**
- Modify: `luna.py`

用一个条件分支替换现有的单次 `blast.analyze()` 调用：有域配置时仅前端 blast 走 `multi_domain_review`，否则走原路径，零破坏。`multi_domain_review` 返回的 `_blast_savings["domains"]` 就是域审查摘要，继续挂在 `report.token_savings["blast"]` 下，先不额外改 reporter 数据结构。

- [ ] **Step 1：找到 luna.py 中的 blast 调用位置**

```bash
grep -n "blast\.analyze\|blast_items" luna.py
```

应看到类似（行号可能稍有偏差）：

```
351:        blast_items, _blast_savings = blast.analyze(
352:            diff, skill_context, cfg,
353:            context_pack=context_pack,
354:            project_root=".",
355:            detail_level=detail_level,
356:        )
```

- [ ] **Step 2：替换那一段调用**

将上面那 6 行替换为：

```python
        if cfg.domains and context_pack is not None:
            from phases.multi_domain_review import multi_domain_review as _multi_review
            blast_items, _blast_savings = _multi_review(
                diff, skill_context, cfg,
                context_pack=context_pack,
                project_root=".",
                detail_level=detail_level,
            )
        else:
            blast_items, _blast_savings = blast.analyze(
                diff, skill_context, cfg,
                context_pack=context_pack,
                project_root=".",
                detail_level=detail_level,
            )
```

注意：不要把 `quality.analyze()`、`backend_review.analyze_backend()`、migration、api_change 改到这个分支里。本计划只改前端 blast 阶段。

- [ ] **Step 3：全量测试，确认无回归**

```bash
python3 -m pytest --tb=short -q
```

Expected: 全部通过（新增数量 = Task 1-5 的测试之和）

- [ ] **Step 4：手动验证——无域配置时行为不变**

```bash
# 确认 config.yaml 里没有 domains 段（或不存在 config.yaml）
python3 -c "
from config import load_config
cfg = load_config('config.yaml')
print('domains:', cfg.domains)
"
```

Expected: `domains: []`

- [ ] **Step 5：手动验证——有域配置时能正常分发**

在项目根目录临时建一个测试 config：

```bash
python3 -c "
from config import Config, DomainEntry
from phases.domain_classifier import classify_symbols_by_domain
from phases.symbol_locator import ChangedSymbol

cfg = Config()
cfg.domains = [DomainEntry('A', ['phases/*']), DomainEntry('B', ['tests/*'])]
syms = [
    ChangedSymbol(file='phases/blast_radius.py', symbol='analyze', symbol_type='function', start_line=1, change_type='modified'),
    ChangedSymbol(file='tests/test_blast_radius.py', symbol='test_foo', symbol_type='function', start_line=1, change_type='modified'),
]
result = classify_symbols_by_domain(syms, cfg.domains)
for domain, s in result.items():
    print(f'{domain}: {[x.symbol for x in s]}')
"
```

Expected:
```
A: ['analyze']
B: ['test_foo']
```

- [ ] **Step 6：commit**

```bash
git add luna.py
git commit -m "feat: wire multi_domain_review into luna.py with fallback"
```

---

## 使用方式

在项目的 `config.yaml` 里加：

```yaml
review:
  max_parallel_domains: 3

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

不加这段配置，Luna 行为与之前完全一致。

多域审查完成后，`report.token_savings["blast"]["domains"]` 会包含类似结构：

```json
[
  {"domain": "私募基金", "symbol_count": 3, "finding_count": 1, "status": "ok", "error": ""},
  {"domain": "公募基金", "symbol_count": 2, "finding_count": 0, "status": "failed", "error": "domain review failed"}
]
```

---

## Self-Review

**Spec coverage check:**

| 需求 | 对应 Task |
|------|----------|
| 按业务域分块 | Task 2 domain_classifier |
| 每块一个 agent 并行审查 | Task 5 ThreadPoolExecutor |
| 相互监督 / 反驳验证 | Task 4 adversarial_verifier + Task 5 domain-scoped context |
| 输出域审查摘要 | Task 5 DomainReviewSummary，Task 6 挂到 token_savings["blast"]["domains"] |
| 不需要 worktree（只读） | 整个方案无 worktree |
| Fallback 不破坏现有流程 | Task 6 条件分支 |
| 配置驱动，不硬编码域名 | Task 1 DomainEntry |
| 并发数可控，避免 rate limit | Task 1 `review.max_parallel_domains` + Task 5 max_workers |
| 域失败不静默 | Task 5 记录 failed summary，全部失败 fallback 单次 blast |
| diff 过滤不泄漏全量上下文 | Task 2 `filter_diff_for_files` no-match 返回空串，Task 5 skipped summary |
| impact path 过滤不漏中间节点 | Task 3 `_path_hits_domain()` 检查整条链路 |
| 不改变其他审查逻辑 | 顶部 Scope boundary + Task 6 注意事项 |

**Type consistency check:**

- `classify_symbols_by_domain` 返回 `dict[str, list[ChangedSymbol]]` → Task 5 直接 `.items()` 遍历 ✓
- `filter_context_pack_for_domain` 入参 `list[ChangedSymbol]`，Task 5 传的也是同类型 ✓
- `blast_analyze` 签名与 `blast_radius.analyze` 完全一致（直接 import as alias）✓
- `adversarial_verify(items, context_snippet, config)` — Task 4 定义，Task 5 调用方式一致 ✓
- `multi_domain_review` 返回 `(list[BlastRadiusItem], dict)`，与 `blast.analyze()` 的返回形状兼容；新增的域摘要放在 dict 内，不影响现有 `report.blast_radius_items` ✓
- 无 `domains` 配置时，Task 5 直接走单次 `blast_analyze()`，Task 6 再做外层 fallback，双保险但不改变结果 ✓

**Placeholder scan:** 无 TBD / TODO / "similar to" 引用。所有步骤均含完整代码。
