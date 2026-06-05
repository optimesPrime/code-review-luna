# tests/test_risk_propagation.py
from phases.risk_propagation import propagate_risk, ImpactPath
from phases.symbol_locator import ChangedSymbol
from phases.context_graph import ContextGraph, GraphNode, GraphEdge


def _make_graph() -> ContextGraph:
    """
    Dependency chain: Order.vue → request.js → stores/user.js
    """
    g = ContextGraph()
    for path in ["src/stores/user.js", "src/utils/request.js", "src/views/Order.vue"]:
        g.nodes[path] = GraphNode(id=path, node_type="file", file=path, name=path)
    g._importers["src/stores/user.js"] = {"src/utils/request.js"}
    g._importers["src/utils/request.js"] = {"src/views/Order.vue"}
    return g


def _symbol(file: str, symbol: str) -> ChangedSymbol:
    return ChangedSymbol(file=file, symbol=symbol, symbol_type="function",
                         start_line=1, change_type="modified")


def test_propagate_includes_changed_file_itself():
    paths = propagate_risk([_symbol("src/stores/user.js", "setTradeUserId")], _make_graph())
    all_files = {f for p in paths for f in p.path}
    assert "src/stores/user.js" in all_files or \
           any("src/stores/user.js" in p.path[0] for p in paths)


def test_propagate_finds_direct_importer():
    paths = propagate_risk([_symbol("src/stores/user.js", "setTradeUserId")], _make_graph())
    all_files = {f for p in paths for f in p.path}
    assert "src/utils/request.js" in all_files


def test_propagate_marks_request_path_high_risk():
    paths = propagate_risk([_symbol("src/stores/user.js", "setTradeUserId")], _make_graph())
    request_paths = [p for p in paths if any("request" in f for f in p.path)]
    assert any(p.risk == "high" for p in request_paths)


def test_propagate_returns_only_origin_for_isolated_file():
    g = ContextGraph()
    g.nodes["src/a.js"] = GraphNode(id="src/a.js", node_type="file", file="src/a.js", name="src/a.js")
    paths = propagate_risk([_symbol("src/a.js", "foo")], g)
    # Only the origin path — no importers
    assert all(len(p.path) <= 1 for p in paths)


def test_propagate_does_not_cycle():
    """Circular imports must not cause infinite BFS."""
    g = ContextGraph()
    for f in ["src/a.js", "src/b.js"]:
        g.nodes[f] = GraphNode(id=f, node_type="file", file=f, name=f)
    g._importers["src/a.js"] = {"src/b.js"}
    g._importers["src/b.js"] = {"src/a.js"}  # cycle
    # Should complete without hanging
    paths = propagate_risk([_symbol("src/a.js", "x")], g)
    assert isinstance(paths, list)
