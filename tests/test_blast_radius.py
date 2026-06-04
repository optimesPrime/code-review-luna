import json
from unittest.mock import patch
from config import Config
from phases.blast_radius import extract_changed_symbols, analyze, BlastRadiusItem


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
