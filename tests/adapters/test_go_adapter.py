import tree_sitter_go as tsgo
from tree_sitter import Language, Parser
from phases.adapters.go_adapter import GoAdapter

ADAPTER = GoAdapter()
_parser = Parser(Language(tsgo.language()))


def _parse(src: str):
    source = src.encode("utf-8")
    return _parser.parse(source).root_node, source


HANDLER_SRC = """\
package handlers

import (
    "github.com/gin-gonic/gin"
)

type OrderHandler struct {
    svc *OrderService
}

func (h *OrderHandler) Submit(c *gin.Context) {
    var req SubmitRequest
    if err := c.ShouldBindJSON(&req); err != nil {
        c.JSON(400, gin.H{"error": err.Error()})
        return
    }
    result, err := h.svc.Submit(req)
    c.JSON(200, result)
}

func SubmitOrder(c *gin.Context) {
    c.JSON(200, gin.H{"ok": true})
}
"""

SERVICE_SRC = """\
package services

import "gorm.io/gorm"

type OrderService struct {
    db *gorm.DB
}

func (s *OrderService) Submit(req SubmitRequest) (OrderResult, error) {
    order := Order{Amount: req.Amount}
    result := s.db.Create(&order)
    return OrderResult{ID: order.ID}, result.Error
}
"""


def test_adapter_name_and_extensions():
    assert ADAPTER.name == "go"
    assert ".go" in ADAPTER.extensions


def test_extract_file_nodes_finds_method():
    root, src = _parse(HANDLER_SRC)
    nodes = ADAPTER.extract_file_nodes(root, src, "handlers/order.go")
    names = [n.name for n in nodes]
    assert "OrderHandler.Submit" in names


def test_extract_file_nodes_finds_free_function():
    root, src = _parse(HANDLER_SRC)
    nodes = ADAPTER.extract_file_nodes(root, src, "handlers/order.go")
    names = [n.name for n in nodes]
    assert "SubmitOrder" in names


def test_extract_file_nodes_gin_handler_type():
    root, src = _parse(HANDLER_SRC)
    nodes = ADAPTER.extract_file_nodes(root, src, "handlers/order.go")
    submit = next(n for n in nodes if n.name == "OrderHandler.Submit")
    assert submit.node_type == "controller_action"


def test_extract_file_nodes_service_method_type():
    root, src = _parse(SERVICE_SRC)
    nodes = ADAPTER.extract_file_nodes(root, src, "services/order_service.go")
    submit = next(n for n in nodes if n.name == "OrderService.Submit")
    assert submit.node_type == "service_method"


def test_extract_file_edges_writes_db():
    root, src = _parse(SERVICE_SRC)
    nodes = ADAPTER.extract_file_nodes(root, src, "services/order_service.go")
    method_index = {n.name: n.id for n in nodes}
    edges = ADAPTER.extract_file_edges(root, src, "services/order_service.go", method_index)
    assert any(e.edge_type == "writes_db" for e in edges)


def test_find_enclosing_symbol_in_method():
    root, src = _parse(HANDLER_SRC)
    # Line 14 is inside Submit method body (c.ShouldBindJSON)
    symbol = ADAPTER.find_enclosing_symbol(root, src, 14, "handlers/order.go", False)
    assert symbol is not None
    assert symbol.symbol == "Submit"
    assert symbol.symbol_type == "controller_action"


def test_find_enclosing_symbol_none_for_import():
    root, src = _parse(HANDLER_SRC)
    symbol = ADAPTER.find_enclosing_symbol(root, src, 4, "handlers/order.go", False)
    assert symbol is None
