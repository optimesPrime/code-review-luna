# tests/adapters/test_csharp_adapter.py
from pathlib import Path
import tree_sitter_c_sharp as tscsharp
from tree_sitter import Language, Parser
from phases.adapters.csharp_adapter import CSharpAdapter
from phases.backend_models import BackendGraphEdge

ADAPTER = CSharpAdapter()

CS = Language(tscsharp.language())
_parser = Parser(CS)


def _parse(src: str):
    source = src.encode("utf-8")
    tree = _parser.parse(source)
    return tree.root_node, source


CONTROLLER_SRC = """\
public class OrderController : ControllerBase
{
    private readonly OrderService _orderService;

    [Authorize]
    [HttpPost("submit")]
    public IActionResult Submit(SubmitOrderRequest request)
    {
        if (request.Amount <= 0) return BadRequest();
        return Ok(_orderService.Submit(request));
    }
}
"""

MODEL_SRC = """\
public class SubmitOrderRequest
{
    public decimal Amount { get; set; }
}
"""


def test_adapter_name_and_extensions():
    assert ADAPTER.name == "csharp"
    assert ".cs" in ADAPTER.extensions


def test_extract_file_nodes_finds_controller_method():
    root, src = _parse(CONTROLLER_SRC)
    nodes = ADAPTER.extract_file_nodes(root, src, "Controllers/OrderController.cs")
    node_ids = [n.id for n in nodes]
    assert "Controllers/OrderController.cs:OrderController.Submit" in node_ids


def test_extract_file_nodes_finds_model_property():
    root, src = _parse(MODEL_SRC)
    nodes = ADAPTER.extract_file_nodes(root, src, "Models/SubmitOrderRequest.cs")
    node_ids = [n.id for n in nodes]
    assert "Models/SubmitOrderRequest.cs:SubmitOrderRequest.Amount" in node_ids


def test_extract_file_nodes_controller_action_type():
    root, src = _parse(CONTROLLER_SRC)
    nodes = ADAPTER.extract_file_nodes(root, src, "Controllers/OrderController.cs")
    submit = next(n for n in nodes if n.name == "OrderController.Submit")
    assert submit.node_type == "controller_action"


def test_extract_file_nodes_model_property_type():
    root, src = _parse(MODEL_SRC)
    nodes = ADAPTER.extract_file_nodes(root, src, "Models/SubmitOrderRequest.cs")
    amount = next(n for n in nodes if n.name == "SubmitOrderRequest.Amount")
    assert amount.node_type == "model_property"


def test_extract_file_nodes_collects_attributes():
    root, src = _parse(CONTROLLER_SRC)
    nodes = ADAPTER.extract_file_nodes(root, src, "Controllers/OrderController.cs")
    submit = next(n for n in nodes if n.name == "OrderController.Submit")
    assert "Authorize" in submit.attributes
    assert "HttpPost" in submit.attributes


def test_extract_file_edges_requires_auth():
    root, src = _parse(CONTROLLER_SRC)
    nodes = ADAPTER.extract_file_nodes(root, src, "Controllers/OrderController.cs")
    method_index = {n.name: n.id for n in nodes}
    edges = ADAPTER.extract_file_edges(root, src, "Controllers/OrderController.cs", method_index)
    assert any(e.edge_type == "requires_auth" for e in edges)


def test_extract_file_edges_exposes_endpoint():
    root, src = _parse(CONTROLLER_SRC)
    nodes = ADAPTER.extract_file_nodes(root, src, "Controllers/OrderController.cs")
    method_index = {n.name: n.id for n in nodes}
    edges = ADAPTER.extract_file_edges(root, src, "Controllers/OrderController.cs", method_index)
    assert any(e.edge_type == "exposes_endpoint" for e in edges)


SERVICE_SRC = """\
public class OrderService
{
    public OrderResult Submit(SubmitOrderRequest request)
    {
        return new OrderResult();
    }
}
"""


def test_extract_file_edges_calls_edge():
    ctrl_root, ctrl_src = _parse(CONTROLLER_SRC)
    svc_root, svc_src = _parse(SERVICE_SRC)

    ctrl_nodes = ADAPTER.extract_file_nodes(ctrl_root, ctrl_src, "Controllers/OrderController.cs")
    svc_nodes = ADAPTER.extract_file_nodes(svc_root, svc_src, "Services/OrderService.cs")
    method_index = {n.name: n.id for n in ctrl_nodes + svc_nodes}

    edges = ADAPTER.extract_file_edges(ctrl_root, ctrl_src, "Controllers/OrderController.cs", method_index)
    call_edges = [e for e in edges if e.edge_type == "calls"]
    assert any(
        e.source == "Controllers/OrderController.cs:OrderController.Submit"
        and e.target == "Services/OrderService.cs:OrderService.Submit"
        for e in call_edges
    )


def test_find_enclosing_symbol_modified_line():
    root, src = _parse(CONTROLLER_SRC)
    # Line 9 is inside Submit method body ("if (request.Amount <= 0)...")
    symbol = ADAPTER.find_enclosing_symbol(root, src, 9, "Controllers/OrderController.cs", False)
    assert symbol is not None
    assert symbol.symbol == "Submit"
    assert symbol.class_name == "OrderController"
    assert symbol.symbol_type == "controller_action"
    assert "Authorize" in symbol.attributes


def test_find_enclosing_symbol_returns_none_for_namespace():
    src = "using System;\nnamespace Foo {}"
    root, source = _parse(src)
    symbol = ADAPTER.find_enclosing_symbol(root, source, 1, "Foo.cs", False)
    assert symbol is None
