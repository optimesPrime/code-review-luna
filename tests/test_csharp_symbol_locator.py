# tests/test_csharp_symbol_locator.py
from pathlib import Path
from phases.csharp_symbol_locator import extract_csharp_changed_symbols_from_diff


CSPROJ_DIFF = """\
diff --git a/Controllers/OrderController.cs b/Controllers/OrderController.cs
index aaa..bbb 100644
--- a/Controllers/OrderController.cs
+++ b/Controllers/OrderController.cs
@@ -8,8 +8,9 @@ public class OrderController : ControllerBase
     [Authorize]
     [HttpPost("submit")]
     public IActionResult Submit(SubmitOrderRequest request)
     {
+        if (request.Amount <= 0) return BadRequest();
         var result = _orderService.Submit(request);
         return Ok(result);
     }
"""


def test_extracts_changed_controller_action(tmp_path: Path):
    source = tmp_path / "Controllers" / "OrderController.cs"
    source.parent.mkdir()
    source.write_text(
        "public class OrderController : ControllerBase\n"
        "{\n"
        "    [Authorize]\n"
        "    [HttpPost(\"submit\")]\n"
        "    public IActionResult Submit(SubmitOrderRequest request)\n"
        "    {\n"
        "        if (request.Amount <= 0) return BadRequest();\n"
        "        var result = _orderService.Submit(request);\n"
        "        return Ok(result);\n"
        "    }\n"
        "}\n",
        encoding="utf-8",
    )

    symbols = extract_csharp_changed_symbols_from_diff(CSPROJ_DIFF, project_root=str(tmp_path))

    assert len(symbols) == 1
    assert symbols[0].file == "Controllers/OrderController.cs"
    assert symbols[0].class_name == "OrderController"
    assert symbols[0].symbol == "Submit"
    assert symbols[0].symbol_type == "controller_action"
    assert "HttpPost" in symbols[0].attributes
    assert "Authorize" in symbols[0].attributes


def test_extracts_changed_model_property(tmp_path: Path):
    diff = """\
diff --git a/Models/SubmitOrderRequest.cs b/Models/SubmitOrderRequest.cs
index aaa..bbb 100644
--- a/Models/SubmitOrderRequest.cs
+++ b/Models/SubmitOrderRequest.cs
@@ -3,4 +3,4 @@ public class SubmitOrderRequest
-    public decimal? Amount { get; set; }
+    public decimal Amount { get; set; }
"""
    source = tmp_path / "Models" / "SubmitOrderRequest.cs"
    source.parent.mkdir()
    source.write_text(
        "public class SubmitOrderRequest\n"
        "{\n"
        "    public decimal Amount { get; set; }\n"
        "}\n",
        encoding="utf-8",
    )

    symbols = extract_csharp_changed_symbols_from_diff(diff, project_root=str(tmp_path))

    assert symbols[0].symbol == "Amount"
    assert symbols[0].symbol_type == "model_property"
    assert symbols[0].class_name == "SubmitOrderRequest"
