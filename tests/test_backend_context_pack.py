# tests/test_backend_context_pack.py
from phases.backend_models import (
    BackendChangedSymbol,
    BackendGraphEdge,
    BackendImpactPath,
    BackendContextPack,
)


def test_backend_context_pack_to_dict_contains_evidence():
    symbol = BackendChangedSymbol(
        file="Controllers/OrderController.cs",
        symbol="Submit",
        symbol_type="controller_action",
        class_name="OrderController",
        start_line=12,
        change_type="modified",
        attributes=["HttpPost", "Authorize"],
        evidence="Controllers/OrderController.cs:12 public IActionResult Submit(...)",
    )
    edge = BackendGraphEdge(
        source="Controllers/OrderController.cs:OrderController.Submit",
        target="Services/OrderService.cs:OrderService.Submit",
        edge_type="calls",
        evidence="line 16: _orderService.Submit(request)",
        confidence="medium",
    )
    impact = BackendImpactPath(
        path=[
            "Controllers/OrderController.cs:OrderController.Submit",
            "Services/OrderService.cs:OrderService.Submit",
        ],
        risk="high",
        confidence="medium",
        evidence="Controller action calls service method after request model change",
        rule_hits=["controller_action_changed", "service_call_chain"],
        needs_human_review=True,
    )
    pack = BackendContextPack(
        changed_symbols=[symbol],
        edges=[edge],
        impact_paths=[impact],
        risk_rules_hit=["controller_action_changed"],
        uncertain_edges=[edge],
        review_focus=["检查接口入口和 service 调用链"],
        related_snippets=["public IActionResult Submit(SubmitOrderRequest request)"],
    )

    data = pack.to_dict()

    assert data["changed_symbols"][0]["symbol_type"] == "controller_action"
    assert data["edges"][0]["edge_type"] == "calls"
    assert data["impact_paths"][0]["risk"] == "high"
    assert data["uncertain_edges"][0]["confidence"] == "medium"
    assert "检查接口入口" in data["review_focus"][0]


from phases.backend_context_pack import build_backend_context_pack


def test_build_backend_pack_generates_review_focus():
    symbol = BackendChangedSymbol(
        file="Controllers/OrderController.cs",
        symbol="Submit",
        symbol_type="controller_action",
        class_name="OrderController",
        start_line=12,
        change_type="modified",
        attributes=["HttpPost"],
        evidence="public IActionResult Submit(...)",
    )
    impact = BackendImpactPath(
        path=["Controllers/OrderController.cs:OrderController.Submit"],
        risk="high",
        confidence="high",
        evidence="controller action changed",
        rule_hits=["controller_action_changed"],
    )

    pack = build_backend_context_pack([symbol], [], [impact])

    assert "controller_action_changed" in pack.risk_rules_hit
    assert any("Controller" in focus or "接口" in focus for focus in pack.review_focus)
