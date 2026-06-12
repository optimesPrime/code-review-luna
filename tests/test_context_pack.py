# tests/test_context_pack.py
from phases.context_pack import build_context_pack, ContextPack
from phases.symbol_locator import ChangedSymbol
from phases.risk_propagation import ImpactPath
from phases.caller_context import CallerSnippet, SymbolCallers


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


def test_context_pack_serializes_file_history():
    pack = build_context_pack([_sym()], [], related_rules=[], related_tests=[])
    pack.file_history = {
        "src/stores/user.js": {
            "flagged_count": 5,
            "recent_issues": [{"timestamp": "2026-06-09 10:00", "risk": "high", "line": 42}],
        }
    }
    d = pack.to_dict()
    assert "file_history" in d
    assert d["file_history"]["src/stores/user.js"]["flagged_count"] == 5


def test_context_pack_empty_file_history_serializes_empty_dict():
    pack = build_context_pack([_sym()], [], related_rules=[], related_tests=[])
    d = pack.to_dict()
    assert d.get("file_history") == {} or "file_history" not in d or d["file_history"] == {}


def test_context_pack_caller_contexts_serialized():
    pack = build_context_pack([_sym()], [], related_rules=[], related_tests=[])
    pack.caller_contexts = [
        SymbolCallers(
            symbol="foo",
            callers=[
                CallerSnippet(
                    file="src/views/Login.vue",
                    line=42,
                    snippet="const result = foo(token)",
                    language="vue",
                )
            ],
            total_count=3,
        )
    ]
    d = pack.to_dict()
    assert "caller_contexts" in d
    assert len(d["caller_contexts"]) == 1
    cc = d["caller_contexts"][0]
    assert cc["symbol"] == "foo"
    assert cc["total_callers_found"] == 3
    assert cc["callers"][0]["file"] == "src/views/Login.vue"
    assert cc["callers"][0]["snippet"] == "const result = foo(token)"


def test_context_pack_caller_contexts_empty_callers_excluded():
    pack = build_context_pack([_sym()], [], related_rules=[], related_tests=[])
    pack.caller_contexts = [
        SymbolCallers(symbol="bar", callers=[], total_count=0),
        SymbolCallers(
            symbol="foo",
            callers=[CallerSnippet(file="a.py", line=1, snippet="foo()", language="python")],
            total_count=1,
        ),
    ]
    d = pack.to_dict()
    symbols_in_output = [cc["symbol"] for cc in d["caller_contexts"]]
    assert "bar" not in symbols_in_output  # 无调用方的符号不应出现
    assert "foo" in symbols_in_output


def test_context_pack_caller_contexts_default_empty():
    pack = build_context_pack([_sym()], [], related_rules=[], related_tests=[])
    d = pack.to_dict()
    assert d.get("caller_contexts") == []
