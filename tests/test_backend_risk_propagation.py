# tests/test_backend_risk_propagation.py
from phases.backend_models import (
    BackendChangedSymbol,
    BackendContextGraph,
    BackendGraphEdge,
    BackendGraphNode,
)
from phases.backend_risk_propagation import propagate_backend_risk


def _symbol(symbol_type: str = "controller_action") -> BackendChangedSymbol:
    return BackendChangedSymbol(
        file="Controllers/OrderController.cs",
        symbol="Submit",
        symbol_type=symbol_type,
        class_name="OrderController",
        start_line=10,
        change_type="modified",
        attributes=["HttpPost", "Authorize"],
        evidence="public IActionResult Submit(...)",
    )


def test_controller_action_change_is_high_risk():
    paths = propagate_backend_risk([_symbol()], BackendContextGraph())
    assert paths[0].risk == "high"
    assert "controller_action_changed" in paths[0].rule_hits


def test_model_property_change_is_high_risk():
    symbol = _symbol("model_property")
    symbol.symbol = "Amount"
    paths = propagate_backend_risk([symbol], BackendContextGraph())
    assert paths[0].risk == "high"
    assert "model_contract_changed" in paths[0].rule_hits


def test_db_write_edge_escalates_risk():
    graph = BackendContextGraph()
    source = "Controllers/OrderController.cs:OrderController.Submit"
    target = "Repositories/OrderRepository.cs:OrderRepository.Save"
    graph.add_node(BackendGraphNode(id=source, node_type="controller_action", file="Controllers/OrderController.cs", name="OrderController.Submit"))
    graph.add_node(BackendGraphNode(id=target, node_type="repository_method", file="Repositories/OrderRepository.cs", name="OrderRepository.Save"))
    graph.add_edge(BackendGraphEdge(
        source=source,
        target=target,
        edge_type="writes_db",
        evidence="SaveChangesAsync()",
        confidence="high",
    ))

    paths = propagate_backend_risk([_symbol()], graph)

    assert any(p.risk == "high" and "db_write_path" in p.rule_hits for p in paths)
