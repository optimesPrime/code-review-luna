# tests/test_backend_pipeline.py
from pathlib import Path

from phases.csharp_symbol_locator import extract_csharp_changed_symbols_from_diff
from phases.csharp_context_graph import build_csharp_backend_graph
from phases.backend_risk_propagation import propagate_backend_risk
from phases.backend_context_pack import build_backend_context_pack


def test_backend_pipeline_builds_context_pack(tmp_path: Path):
    controller = tmp_path / "Controllers" / "OrderController.cs"
    service = tmp_path / "Services" / "OrderService.cs"
    controller.parent.mkdir()
    service.parent.mkdir()

    controller.write_text(
        "public class OrderController : ControllerBase\n"
        "{\n"
        "    private readonly OrderService _orderService;\n"
        "    [HttpPost(\"submit\")]\n"
        "    public IActionResult Submit(SubmitOrderRequest request)\n"
        "    {\n"
        "        if (request.Amount <= 0) return BadRequest();\n"
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
    diff = """\
diff --git a/Controllers/OrderController.cs b/Controllers/OrderController.cs
index aaa..bbb 100644
--- a/Controllers/OrderController.cs
+++ b/Controllers/OrderController.cs
@@ -6,6 +6,7 @@ public class OrderController : ControllerBase
     public IActionResult Submit(SubmitOrderRequest request)
     {
+        if (request.Amount <= 0) return BadRequest();
         return Ok(_orderService.Submit(request));
     }
"""

    symbols = extract_csharp_changed_symbols_from_diff(diff, project_root=str(tmp_path))
    graph = build_csharp_backend_graph(str(tmp_path))
    paths = propagate_backend_risk(symbols, graph)
    pack = build_backend_context_pack(symbols, graph.edges, paths)
    data = pack.to_dict()

    assert data["changed_symbols"][0]["symbol"] == "Submit"
    assert any(edge["edge_type"] == "calls" for edge in data["edges"])
    assert any(path["risk"] == "high" for path in data["impact_paths"])
    assert data["review_focus"]
