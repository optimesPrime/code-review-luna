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
    ctx = build_adversarial_context("", {"src/a.ts"}, pack)
    assert "funcA" in ctx
    assert "funcB" not in ctx


def test_build_context_no_callers_shows_placeholder():
    sym = _sym()
    pack = build_context_pack([sym], [], related_rules=[], related_tests=[])
    ctx = build_adversarial_context("", {sym.file}, pack)
    assert "（无）" in ctx
