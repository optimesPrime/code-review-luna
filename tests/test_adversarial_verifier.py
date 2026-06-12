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
