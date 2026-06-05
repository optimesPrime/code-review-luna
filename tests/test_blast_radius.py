import json
from unittest.mock import patch
from config import Config
from phases.blast_radius import extract_changed_symbols, analyze, BlastRadiusItem
from phases.context_pack import ContextPack
from phases.symbol_locator import ChangedSymbol
from phases.risk_propagation import ImpactPath


SAMPLE_DIFF = """\
diff --git a/src/composables/useAuth.js b/src/composables/useAuth.js
index 1234567..abcdefg 100644
--- a/src/composables/useAuth.js
+++ b/src/composables/useAuth.js
@@ -1,5 +1,10 @@
+export function refreshToken(token) {
+  return fetch('/api/refresh', { method: 'POST', body: token })
+}
+
+export const clearSession = () => {
+  localStorage.removeItem('token')
+}
"""


def test_extract_changed_symbols():
    symbols = extract_changed_symbols(SAMPLE_DIFF)
    assert "refreshToken" in symbols
    assert "clearSession" in symbols


def test_extract_no_symbols_when_only_deletions():
    diff = "diff --git a/foo.js b/foo.js\n-removed line\n"
    symbols = extract_changed_symbols(diff)
    assert symbols == []


def test_analyze_returns_blast_radius_items(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    cfg = Config()
    api_response = json.dumps([{
        "file": "router/index.js",
        "line": 45,
        "symbol": "refreshToken",
        "risk": "high",
        "confidence": "high",
        "reason": "路由守卫依赖此函数",
        "suggestion": "增加 token 有效性校验",
        "needs_human_review": False,
    }])
    with patch("phases.blast_radius.call_claude", return_value=api_response), \
         patch("phases.blast_radius.find_usages_in_project", return_value=""):
        items = analyze(SAMPLE_DIFF, "", cfg)
    assert len(items) == 1
    assert items[0].file == "router/index.js"
    assert items[0].risk == "high"
    assert items[0].symbol == "refreshToken"


def test_analyze_returns_empty_on_invalid_json(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    cfg = Config()
    with patch("phases.blast_radius.call_claude", return_value="无法分析"), \
         patch("phases.blast_radius.find_usages_in_project", return_value=""):
        items = analyze(SAMPLE_DIFF, "", cfg)
    assert items == []


def test_analyze_with_context_pack(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    cfg = Config()
    pack = ContextPack(
        changed_symbols=[
            ChangedSymbol(
                file="src/stores/user.js", symbol="setTradeUserId",
                symbol_type="function", start_line=6, change_type="modified",
            )
        ],
        impact_paths=[
            ImpactPath(
                path=["src/stores/user.js:setTradeUserId", "src/utils/request.js"],
                risk="high", confidence="high",
                evidence="request interceptor depends on this symbol",
            )
        ],
        related_rules=["X-Trade-UserId header must exist on every request"],
        related_tests=[],
        review_focus=["verify header presence"],
    )
    api_response = json.dumps([{
        "file": "src/utils/request.js",
        "line": 12,
        "symbol": "setTradeUserId",
        "risk": "high",
        "confidence": "high",
        "reason": "request interceptor uses this value",
        "suggestion": None,
        "needs_human_review": False,
    }])
    diff = "diff --git a/src/stores/user.js b/src/stores/user.js\n"
    with patch("phases.blast_radius.call_claude", return_value=api_response):
        items = analyze(diff, "", cfg, context_pack=pack)
    assert len(items) == 1
    assert items[0].file == "src/utils/request.js"
    assert items[0].risk == "high"


def test_analyze_without_context_pack_uses_fallback(monkeypatch):
    """Existing behavior must be unchanged when context_pack is None."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    cfg = Config()
    api_response = json.dumps([{
        "file": "router/index.js", "line": 45, "symbol": "refreshToken",
        "risk": "high", "confidence": "high", "reason": "路由守卫",
        "suggestion": None, "needs_human_review": False,
    }])
    diff = "diff --git a/src/useAuth.js b/src/useAuth.js\n+export function refreshToken() {}\n"
    with patch("phases.blast_radius.call_claude", return_value=api_response), \
         patch("phases.blast_radius.find_usages_in_project", return_value=""):
        items = analyze(diff, "", cfg)  # no context_pack
    assert items[0].file == "router/index.js"
