import tree_sitter_python as tspy
from tree_sitter import Language, Parser
from phases.adapters.python_adapter import PythonAdapter

ADAPTER = PythonAdapter()
_parser = Parser(Language(tspy.language()))


def _parse(src: str):
    source = src.encode("utf-8")
    return _parser.parse(source).root_node, source


ROUTER_SRC = """\
from fastapi import APIRouter, Depends, Security
from app.auth import get_current_user

router = APIRouter()


@router.post("/orders")
async def submit_order(req: SubmitRequest, user=Depends(get_current_user)):
    return await order_service.submit(req)


@router.get("/orders/{id}")
async def get_order(order_id: int, user=Depends(get_current_user)):
    return await order_service.get(order_id)
"""

SERVICE_SRC = """\
class OrderService:
    async def submit(self, req: SubmitRequest) -> OrderResult:
        await self.db.commit()
        return OrderResult()

    async def get(self, order_id: int) -> OrderResult:
        return await self.db.get(OrderResult, order_id)
"""


def test_adapter_name_and_extensions():
    assert ADAPTER.name == "python"
    assert ".py" in ADAPTER.extensions


def test_extract_file_nodes_finds_route_handler():
    root, src = _parse(ROUTER_SRC)
    nodes = ADAPTER.extract_file_nodes(root, src, "routers/orders.py")
    names = [n.name for n in nodes]
    assert "submit_order" in names


def test_extract_file_nodes_route_handler_type():
    root, src = _parse(ROUTER_SRC)
    nodes = ADAPTER.extract_file_nodes(root, src, "routers/orders.py")
    submit = next(n for n in nodes if n.name == "submit_order")
    assert submit.node_type == "controller_action"


def test_extract_file_nodes_collects_decorator():
    root, src = _parse(ROUTER_SRC)
    nodes = ADAPTER.extract_file_nodes(root, src, "routers/orders.py")
    submit = next(n for n in nodes if n.name == "submit_order")
    assert any("post" in a.lower() for a in submit.attributes)


def test_extract_file_nodes_class_method():
    root, src = _parse(SERVICE_SRC)
    nodes = ADAPTER.extract_file_nodes(root, src, "services/order_service.py")
    names = [n.name for n in nodes]
    assert "OrderService.submit" in names


def test_extract_file_nodes_service_method_type():
    root, src = _parse(SERVICE_SRC)
    nodes = ADAPTER.extract_file_nodes(root, src, "services/order_service.py")
    submit = next(n for n in nodes if n.name == "OrderService.submit")
    assert submit.node_type == "service_method"


def test_extract_file_edges_requires_auth():
    root, src = _parse(ROUTER_SRC)
    nodes = ADAPTER.extract_file_nodes(root, src, "routers/orders.py")
    method_index = {n.name: n.id for n in nodes}
    edges = ADAPTER.extract_file_edges(root, src, "routers/orders.py", method_index)
    assert any(e.edge_type == "requires_auth" for e in edges)


def test_extract_file_edges_writes_db():
    root, src = _parse(SERVICE_SRC)
    nodes = ADAPTER.extract_file_nodes(root, src, "services/order_service.py")
    method_index = {n.name: n.id for n in nodes}
    edges = ADAPTER.extract_file_edges(root, src, "services/order_service.py", method_index)
    assert any(e.edge_type == "writes_db" for e in edges)


def test_find_enclosing_symbol_inside_route():
    root, src = _parse(ROUTER_SRC)
    # Line 9 is inside submit_order body
    symbol = ADAPTER.find_enclosing_symbol(root, src, 9, "routers/orders.py", False)
    assert symbol is not None
    assert symbol.symbol == "submit_order"
    assert symbol.symbol_type == "controller_action"


def test_find_enclosing_symbol_none_for_import():
    root, src = _parse(ROUTER_SRC)
    symbol = ADAPTER.find_enclosing_symbol(root, src, 1, "routers/orders.py", False)
    assert symbol is None
