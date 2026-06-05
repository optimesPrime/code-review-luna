# tests/test_backend_review.py
import json
from unittest.mock import patch

from phases.backend_models import BackendContextPack
from phases.backend_review import analyze_backend
from config import Config


def test_analyze_backend_parses_json_items(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    pack = BackendContextPack(
        changed_symbols=[],
        edges=[],
        impact_paths=[],
        risk_rules_hit=[],
        uncertain_edges=[],
        review_focus=[],
        related_snippets=[],
    )
    raw = json.dumps([{
        "file": "Controllers/OrderController.cs",
        "line": 12,
        "symbol": "Submit",
        "risk": "high",
        "confidence": "high",
        "category": "controller",
        "reason": "接口入口缺少失败分支处理",
        "evidence": "OrderService.Submit result is returned as Ok without status check",
        "suggestion": "检查 service 失败结果并返回合适状态码",
        "needs_human_review": True,
    }])

    with patch("phases.backend_review.call_claude", return_value=raw):
        items = analyze_backend(pack, "diff text", "", Config())

    assert len(items) == 1
    assert items[0].file == "Controllers/OrderController.cs"
    assert items[0].risk == "high"
    assert items[0].category == "controller"
