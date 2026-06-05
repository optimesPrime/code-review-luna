from phases.backend_language_adapter import LanguageAdapter


def test_language_adapter_protocol_is_importable():
    assert LanguageAdapter is not None


import json
from pathlib import Path
from unittest.mock import MagicMock
from phases.backend_graph_engine import build_graph, find_symbols_from_diff, save_graph, load_graph
from phases.backend_models import BackendChangedSymbol, BackendContextGraph, BackendGraphEdge, BackendGraphNode


def _make_mock_adapter(ext=".cs"):
    adapter = MagicMock()
    adapter.name = "mock"
    adapter.extensions = (ext,)
    adapter.get_language.return_value = None
    adapter.extract_file_nodes.return_value = [
        BackendGraphNode(id="foo.cs:Foo.Bar", node_type="method", file="foo.cs", name="Foo.Bar", line=1)
    ]
    adapter.extract_file_edges.return_value = []
    adapter.find_enclosing_symbol.return_value = BackendChangedSymbol(
        file="foo.cs", symbol="Bar", symbol_type="method",
        class_name="Foo", start_line=1, change_type="modified",
    )
    return adapter


def test_build_graph_calls_adapter_per_file(tmp_path):
    (tmp_path / "foo.cs").write_text("class Foo { void Bar() {} }", encoding="utf-8")
    adapter = _make_mock_adapter()

    import phases.backend_graph_engine as eng
    original_parse = eng._parse_file
    eng._parse_file = lambda path, adapter: (MagicMock(), path.read_bytes())
    try:
        graph = build_graph(adapter, project_root=str(tmp_path))
    finally:
        eng._parse_file = original_parse

    assert "foo.cs:Foo.Bar" in graph.nodes
    adapter.extract_file_nodes.assert_called_once()


def test_save_and_load_graph_roundtrip(tmp_path):
    graph = BackendContextGraph()
    graph.add_node(BackendGraphNode(id="a.cs:A.B", node_type="method", file="a.cs", name="A.B", line=1))
    graph.add_edge(BackendGraphEdge(source="a.cs:A.B", target="b.cs:C.D", edge_type="calls", evidence="line 5", confidence="medium"))
    cache = tmp_path / "g.json"
    save_graph(graph, str(cache))

    loaded = load_graph(str(cache))
    assert loaded is not None
    assert "a.cs:A.B" in loaded.nodes
    assert loaded.edges[0].edge_type == "calls"


def test_load_graph_returns_none_for_missing():
    assert load_graph("/nonexistent/graph.json") is None


def test_find_symbols_from_diff_uses_adapter(tmp_path):
    cs = tmp_path / "Controllers" / "Foo.cs"
    cs.parent.mkdir(parents=True, exist_ok=True)
    cs.write_text("class Foo { void Bar() {} }", encoding="utf-8")

    diff = """\
diff --git a/Controllers/Foo.cs b/Controllers/Foo.cs
--- a/Controllers/Foo.cs
+++ b/Controllers/Foo.cs
@@ -1,1 +1,2 @@
+    // changed
"""
    adapter = _make_mock_adapter()
    import phases.backend_graph_engine as eng
    original_parse = eng._parse_file
    eng._parse_file = lambda path, adapter: (MagicMock(), path.read_bytes())
    try:
        symbols = find_symbols_from_diff(diff, adapter, project_root=str(tmp_path))
    finally:
        eng._parse_file = original_parse

    assert len(symbols) == 1
    assert symbols[0].symbol == "Bar"
