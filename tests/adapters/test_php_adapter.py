import tree_sitter_php as tsphp
from tree_sitter import Language, Parser
from phases.adapters.php_adapter import PhpAdapter

ADAPTER = PhpAdapter()
_parser = Parser(Language(tsphp.language_php()))


def _parse(src: str):
    source = src.encode("utf-8")
    return _parser.parse(source).root_node, source


CONTROLLER_SRC = """\
<?php
namespace App\\Http\\Controllers;

use App\\Services\\OrderService;
use Illuminate\\Http\\Request;

class OrderController extends Controller
{
    protected $orderService;

    public function __construct(OrderService $orderService)
    {
        $this->orderService = $orderService;
        $this->middleware('auth');
    }

    public function submit(Request $request)
    {
        $this->authorize('submit-order');
        $data = $request->validated();
        return response()->json($this->orderService->submit($data));
    }
}
"""

SERVICE_SRC = """\
<?php
namespace App\\Services;

class OrderService
{
    public function submit(array $data)
    {
        $order = new Order($data);
        $order->save();
        return $order;
    }
}
"""


def test_adapter_name_and_extensions():
    assert ADAPTER.name == "php"
    assert ".php" in ADAPTER.extensions


def test_extract_file_nodes_finds_controller_method():
    root, src = _parse(CONTROLLER_SRC)
    nodes = ADAPTER.extract_file_nodes(root, src, "app/Http/Controllers/OrderController.php")
    names = [n.name for n in nodes]
    assert "OrderController.submit" in names


def test_extract_file_nodes_controller_action_type():
    root, src = _parse(CONTROLLER_SRC)
    nodes = ADAPTER.extract_file_nodes(root, src, "app/Http/Controllers/OrderController.php")
    submit = next(n for n in nodes if n.name == "OrderController.submit")
    assert submit.node_type == "controller_action"


def test_extract_file_nodes_service_method_type():
    root, src = _parse(SERVICE_SRC)
    nodes = ADAPTER.extract_file_nodes(root, src, "app/Services/OrderService.php")
    submit = next(n for n in nodes if n.name == "OrderService.submit")
    assert submit.node_type == "service_method"


def test_extract_file_edges_requires_auth():
    root, src = _parse(CONTROLLER_SRC)
    nodes = ADAPTER.extract_file_nodes(root, src, "app/Http/Controllers/OrderController.php")
    method_index = {n.name: n.id for n in nodes}
    edges = ADAPTER.extract_file_edges(root, src, "app/Http/Controllers/OrderController.php", method_index)
    assert any(e.edge_type == "requires_auth" for e in edges)


def test_extract_file_edges_writes_db():
    root, src = _parse(SERVICE_SRC)
    nodes = ADAPTER.extract_file_nodes(root, src, "app/Services/OrderService.php")
    method_index = {n.name: n.id for n in nodes}
    edges = ADAPTER.extract_file_edges(root, src, "app/Services/OrderService.php", method_index)
    assert any(e.edge_type == "writes_db" for e in edges)


def test_find_enclosing_symbol_inside_method():
    root, src = _parse(CONTROLLER_SRC)
    # Line 20 is inside submit() body
    symbol = ADAPTER.find_enclosing_symbol(root, src, 20, "app/Http/Controllers/OrderController.php", False)
    assert symbol is not None
    assert symbol.symbol == "submit"
    assert symbol.class_name == "OrderController"


def test_find_enclosing_symbol_none_for_namespace():
    root, src = _parse(CONTROLLER_SRC)
    symbol = ADAPTER.find_enclosing_symbol(root, src, 2, "app/Http/Controllers/OrderController.php", False)
    assert symbol is None
