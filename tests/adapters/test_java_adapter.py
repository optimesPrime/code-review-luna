import tree_sitter_java as tsjava
from tree_sitter import Language, Parser
from phases.adapters.java_adapter import JavaAdapter

ADAPTER = JavaAdapter()
_parser = Parser(Language(tsjava.language()))


def _parse(src: str):
    source = src.encode("utf-8")
    return _parser.parse(source).root_node, source


CONTROLLER_SRC = """\
package com.example;
import org.springframework.web.bind.annotation.*;
import org.springframework.security.access.prepost.PreAuthorize;

@RestController
@RequestMapping("/orders")
public class OrderController {
    private final OrderService orderService;

    public OrderController(OrderService orderService) {
        this.orderService = orderService;
    }

    @PreAuthorize("isAuthenticated()")
    @PostMapping("/submit")
    public ResponseEntity<OrderResult> submit(@RequestBody SubmitRequest req) {
        return ResponseEntity.ok(orderService.submit(req));
    }
}
"""

SERVICE_SRC = """\
package com.example;
import org.springframework.stereotype.Service;

@Service
public class OrderService {
    public OrderResult submit(SubmitRequest req) {
        return new OrderResult();
    }
}
"""


def test_adapter_name_and_extensions():
    assert ADAPTER.name == "java"
    assert ".java" in ADAPTER.extensions


def test_extract_file_nodes_finds_controller_method():
    root, src = _parse(CONTROLLER_SRC)
    nodes = ADAPTER.extract_file_nodes(root, src, "controllers/OrderController.java")
    ids = [n.id for n in nodes]
    assert "controllers/OrderController.java:OrderController.submit" in ids


def test_extract_file_nodes_controller_action_type():
    root, src = _parse(CONTROLLER_SRC)
    nodes = ADAPTER.extract_file_nodes(root, src, "controllers/OrderController.java")
    submit = next(n for n in nodes if n.name == "OrderController.submit")
    assert submit.node_type == "controller_action"


def test_extract_file_nodes_service_method_type():
    root, src = _parse(SERVICE_SRC)
    nodes = ADAPTER.extract_file_nodes(root, src, "services/OrderService.java")
    submit = next(n for n in nodes if n.name == "OrderService.submit")
    assert submit.node_type == "service_method"


def test_extract_file_nodes_collects_annotations():
    root, src = _parse(CONTROLLER_SRC)
    nodes = ADAPTER.extract_file_nodes(root, src, "controllers/OrderController.java")
    submit = next(n for n in nodes if n.name == "OrderController.submit")
    assert "PostMapping" in submit.attributes
    assert "PreAuthorize" in submit.attributes


def test_extract_file_edges_requires_auth():
    root, src = _parse(CONTROLLER_SRC)
    nodes = ADAPTER.extract_file_nodes(root, src, "controllers/OrderController.java")
    method_index = {n.name: n.id for n in nodes}
    edges = ADAPTER.extract_file_edges(root, src, "controllers/OrderController.java", method_index)
    assert any(e.edge_type == "requires_auth" for e in edges)


def test_extract_file_edges_exposes_endpoint():
    root, src = _parse(CONTROLLER_SRC)
    nodes = ADAPTER.extract_file_nodes(root, src, "controllers/OrderController.java")
    method_index = {n.name: n.id for n in nodes}
    edges = ADAPTER.extract_file_edges(root, src, "controllers/OrderController.java", method_index)
    assert any(e.edge_type == "exposes_endpoint" for e in edges)


def test_extract_file_edges_calls_service():
    ctrl_root, ctrl_src = _parse(CONTROLLER_SRC)
    svc_root, svc_src = _parse(SERVICE_SRC)
    ctrl_nodes = ADAPTER.extract_file_nodes(ctrl_root, ctrl_src, "controllers/OrderController.java")
    svc_nodes = ADAPTER.extract_file_nodes(svc_root, svc_src, "services/OrderService.java")
    method_index = {n.name: n.id for n in ctrl_nodes + svc_nodes}
    edges = ADAPTER.extract_file_edges(ctrl_root, ctrl_src, "controllers/OrderController.java", method_index)
    assert any(
        e.edge_type == "calls"
        and e.source == "controllers/OrderController.java:OrderController.submit"
        and e.target == "services/OrderService.java:OrderService.submit"
        for e in edges
    )


def test_find_enclosing_symbol_inside_method():
    root, src = _parse(CONTROLLER_SRC)
    # Line 17 is inside submit() body
    symbol = ADAPTER.find_enclosing_symbol(root, src, 17, "controllers/OrderController.java", False)
    assert symbol is not None
    assert symbol.symbol == "submit"
    assert symbol.class_name == "OrderController"
    assert symbol.symbol_type == "controller_action"


def test_find_enclosing_symbol_returns_none_for_import():
    root, src = _parse(CONTROLLER_SRC)
    symbol = ADAPTER.find_enclosing_symbol(root, src, 2, "controllers/OrderController.java", False)
    assert symbol is None
