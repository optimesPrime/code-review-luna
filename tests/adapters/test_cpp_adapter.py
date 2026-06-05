import tree_sitter_cpp as tscpp
from tree_sitter import Language, Parser
from phases.adapters.cpp_adapter import CppAdapter

ADAPTER = CppAdapter()
_parser = Parser(Language(tscpp.language()))


def _parse(src: str):
    source = src.encode("utf-8")
    return _parser.parse(source).root_node, source


SERVICE_SRC = """\
#include <grpcpp/grpcpp.h>
#include <mutex>

class OrderServiceImpl final : public OrderService::Service {
public:
    grpc::Status Submit(grpc::ServerContext* ctx,
                        const SubmitRequest* req,
                        OrderResult* resp) override {
        std::lock_guard<std::mutex> lock(mutex_);
        resp->set_id(repo_.Save(*req));
        return grpc::Status::OK;
    }

private:
    OrderRepository repo_;
    std::mutex mutex_;
};
"""

FREE_FUNC_SRC = """\
#include "order_service.h"

OrderResult ProcessOrder(const SubmitRequest& req) {
    auto result = service.Submit(req);
    return result;
}
"""


def test_adapter_name_and_extensions():
    assert ADAPTER.name == "cpp"
    assert ".cpp" in ADAPTER.extensions
    assert ".h" in ADAPTER.extensions


def test_extract_file_nodes_finds_class_method():
    root, src = _parse(SERVICE_SRC)
    nodes = ADAPTER.extract_file_nodes(root, src, "src/order_service.cpp")
    names = [n.name for n in nodes]
    assert "OrderServiceImpl.Submit" in names


def test_extract_file_nodes_finds_free_function():
    root, src = _parse(FREE_FUNC_SRC)
    nodes = ADAPTER.extract_file_nodes(root, src, "src/process.cpp")
    names = [n.name for n in nodes]
    assert "ProcessOrder" in names


def test_extract_file_nodes_grpc_handler_type():
    root, src = _parse(SERVICE_SRC)
    nodes = ADAPTER.extract_file_nodes(root, src, "src/order_service.cpp")
    submit = next(n for n in nodes if n.name == "OrderServiceImpl.Submit")
    assert submit.node_type == "controller_action"


def test_extract_file_edges_concurrency():
    root, src = _parse(SERVICE_SRC)
    nodes = ADAPTER.extract_file_nodes(root, src, "src/order_service.cpp")
    method_index = {n.name: n.id for n in nodes}
    edges = ADAPTER.extract_file_edges(root, src, "src/order_service.cpp", method_index)
    assert any(e.edge_type == "concurrency_boundary" for e in edges)


def test_find_enclosing_symbol_inside_method():
    root, src = _parse(SERVICE_SRC)
    # Line 9 is inside Submit() body (lock_guard line)
    symbol = ADAPTER.find_enclosing_symbol(root, src, 9, "src/order_service.cpp", False)
    assert symbol is not None
    assert symbol.symbol == "Submit"
    assert symbol.class_name == "OrderServiceImpl"


def test_find_enclosing_symbol_none_for_include():
    root, src = _parse(SERVICE_SRC)
    symbol = ADAPTER.find_enclosing_symbol(root, src, 1, "src/order_service.cpp", False)
    assert symbol is None
