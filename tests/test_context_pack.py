# tests/test_context_pack.py
from phases.context_pack import build_context_pack, ContextPack
from phases.symbol_locator import ChangedSymbol
from phases.risk_propagation import ImpactPath


def _sym(symbol: str = "foo") -> ChangedSymbol:
    return ChangedSymbol(
        file="src/stores/user.js", symbol=symbol,
        symbol_type="function", start_line=6, change_type="modified"
    )


def _path(risk: str = "high", needs_review: bool = False) -> ImpactPath:
    return ImpactPath(
        path=["src/stores/user.js:foo", "src/utils/request.js"],
        risk=risk, confidence="high",
        evidence="request interceptor depends on store",
        needs_human_review=needs_review,
    )


def test_pack_includes_changed_symbols():
    pack = build_context_pack([_sym()], [], related_rules=[], related_tests=[])
    assert len(pack.changed_symbols) == 1
    assert pack.changed_symbols[0].symbol == "foo"


def test_pack_generates_review_focus_for_high_risk():
    pack = build_context_pack([_sym()], [_path(risk="high")],
                              related_rules=[], related_tests=[])
    assert any("高风险" in f or "high" in f.lower() or "request" in f.lower()
               for f in pack.review_focus)


def test_pack_review_focus_mentions_human_review():
    pack = build_context_pack([_sym()], [_path(needs_review=True)],
                              related_rules=[], related_tests=[])
    assert any("人工" in f or "确认" in f for f in pack.review_focus)


def test_pack_to_dict_has_required_keys():
    pack = build_context_pack([_sym()], [_path()],
                              related_rules=["must have X-Trade-UserId"],
                              related_tests=[])
    d = pack.to_dict()
    for key in ("changed_symbols", "impact_paths", "related_rules", "related_tests", "review_focus"):
        assert key in d, f"Missing key: {key}"


def test_pack_to_dict_related_rules_preserved():
    pack = build_context_pack([_sym()], [],
                              related_rules=["rule A", "rule B"],
                              related_tests=["test_login"])
    d = pack.to_dict()
    assert d["related_rules"] == ["rule A", "rule B"]
    assert d["related_tests"] == ["test_login"]
