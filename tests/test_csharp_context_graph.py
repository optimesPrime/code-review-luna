# tests/test_csharp_context_graph.py
from pathlib import Path
from phases.adapters.csharp_adapter import CSHARP_ADAPTER
from phases.backend_graph_engine import build_graph, save_graph, load_graph


def test_builds_controller_to_service_edge(tmp_path: Path):
    controller = tmp_path / "Controllers" / "OrderController.cs"
    service = tmp_path / "Services" / "OrderService.cs"
    controller.parent.mkdir()
    service.parent.mkdir()

    controller.write_text(
        "public class OrderController : ControllerBase\n"
        "{\n"
        "    private readonly OrderService _orderService;\n"
        "    [Authorize]\n"
        "    [HttpPost(\"submit\")]\n"
        "    public IActionResult Submit(SubmitOrderRequest request)\n"
        "    {\n"
        "        return Ok(_orderService.Submit(request));\n"
        "    }\n"
        "}\n",
        encoding="utf-8",
    )
    service.write_text(
        "public class OrderService\n"
        "{\n"
        "    public OrderResult Submit(SubmitOrderRequest request)\n"
        "    {\n"
        "        return new OrderResult();\n"
        "    }\n"
        "}\n",
        encoding="utf-8",
    )

    graph = build_graph(CSHARP_ADAPTER, project_root=str(tmp_path))

    assert "Controllers/OrderController.cs:OrderController.Submit" in graph.nodes
    assert "Services/OrderService.cs:OrderService.Submit" in graph.nodes
    assert any(
        e.source == "Controllers/OrderController.cs:OrderController.Submit"
        and e.target == "Services/OrderService.cs:OrderService.Submit"
        and e.edge_type == "calls"
        for e in graph.edges
    )


def test_marks_authorize_attribute_as_auth_edge(tmp_path: Path):
    controller = tmp_path / "Controllers" / "SecureController.cs"
    controller.parent.mkdir()
    controller.write_text(
        "public class SecureController : ControllerBase\n"
        "{\n"
        "    [Authorize]\n"
        "    [HttpGet(\"me\")]\n"
        "    public IActionResult Me() { return Ok(); }\n"
        "}\n",
        encoding="utf-8",
    )

    graph = build_graph(CSHARP_ADAPTER, project_root=str(tmp_path))

    assert any(e.edge_type == "requires_auth" for e in graph.edges)


def test_save_and_load_graph_roundtrip(tmp_path: Path):
    controller = tmp_path / "Controllers" / "OrderController.cs"
    controller.parent.mkdir()
    controller.write_text(
        "public class OrderController : ControllerBase\n"
        "{\n"
        "    [HttpPost]\n"
        "    public IActionResult Submit() { return Ok(); }\n"
        "}\n",
        encoding="utf-8",
    )
    graph = build_graph(CSHARP_ADAPTER, project_root=str(tmp_path))
    cache = tmp_path / "graph.json"
    save_graph(graph, str(cache))

    loaded = load_graph(str(cache))
    assert loaded is not None
    assert set(loaded.nodes.keys()) == set(graph.nodes.keys())
    assert len(loaded.edges) == len(graph.edges)


def test_load_graph_returns_none_for_missing():
    assert load_graph("/nonexistent/graph.json") is None
