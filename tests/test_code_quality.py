import json
from unittest.mock import patch
from config import Config
from phases.code_quality import analyze, CodeQualityItem


SAMPLE_DIFF = """\
diff --git a/src/views/Login.vue b/src/views/Login.vue
--- a/src/views/Login.vue
+++ b/src/views/Login.vue
@@ -10,6 +10,12 @@
+const handleSubmit = async () => {
+  localStorage.removeItem('token')
+  const res = await login(form)
+  if (res.code === 200) {
+    localStorage.removeItem('token')
+    router.push('/')
+  }
+}
"""


def test_analyze_returns_quality_items(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    cfg = Config()
    api_response = json.dumps([{
        "file": "src/views/Login.vue",
        "line": 14,
        "issue_type": "redundant",
        "description": "重复的 token 清理逻辑",
        "evidence": "第 11 行与第 14 行均调用 localStorage.removeItem('token')",
        "risk": "low",
        "confidence": "high",
        "suggestion": "删除第 14 行重复调用",
    }])
    with patch("phases.code_quality.call_claude", return_value=api_response):
        items, _ = analyze(SAMPLE_DIFF, "", cfg)
    assert len(items) == 1
    assert items[0].issue_type == "redundant"
    assert items[0].risk == "low"


def test_analyze_returns_empty_on_no_issues(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    cfg = Config()
    with patch("phases.code_quality.call_claude", return_value="[]"):
        items, _ = analyze(SAMPLE_DIFF, "", cfg)
    assert items == []


def test_analyze_returns_empty_on_invalid_json(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    cfg = Config()
    with patch("phases.code_quality.call_claude", return_value="无问题"):
        items, _ = analyze(SAMPLE_DIFF, "", cfg)
    assert items == []
