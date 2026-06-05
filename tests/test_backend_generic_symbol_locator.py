# tests/test_backend_generic_symbol_locator.py
from pathlib import Path

from phases.backend_generic_symbol_locator import extract_generic_backend_symbols_from_diff


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_detects_java_spring_controller_method(tmp_path: Path):
    _write(tmp_path / "src" / "OrderController.java", """
@RestController
public class OrderController {
    @PostMapping("/submit")
    public OrderResult submit(SubmitOrderRequest request) {
        return service.submit(request);
    }
}
""")
    diff = """\
diff --git a/src/OrderController.java b/src/OrderController.java
--- a/src/OrderController.java
+++ b/src/OrderController.java
@@ -4,3 +4,4 @@ public class OrderController {
+        validate(request);
"""

    symbols = extract_generic_backend_symbols_from_diff(diff, project_root=str(tmp_path), languages=["java"])

    assert symbols[0].symbol == "submit"
    assert symbols[0].symbol_type == "controller_action"
    assert symbols[0].class_name == "OrderController"


def test_detects_python_fastapi_route(tmp_path: Path):
    _write(tmp_path / "app" / "orders.py", """
@router.post("/orders")
def submit_order(request: SubmitOrderRequest):
    return service.submit(request)
""")
    diff = """\
diff --git a/app/orders.py b/app/orders.py
--- a/app/orders.py
+++ b/app/orders.py
@@ -3,2 +3,3 @@ def submit_order(request: SubmitOrderRequest):
+    validate_amount(request)
"""

    symbols = extract_generic_backend_symbols_from_diff(diff, project_root=str(tmp_path), languages=["python"])

    assert symbols[0].symbol == "submit_order"
    assert symbols[0].symbol_type == "controller_action"


def test_detects_nodejs_route_handler(tmp_path: Path):
    _write(tmp_path / "src" / "orders.ts", """
router.post("/orders", async function submitOrder(req, res) {
  return service.submit(req.body)
})
""")
    diff = """\
diff --git a/src/orders.ts b/src/orders.ts
--- a/src/orders.ts
+++ b/src/orders.ts
@@ -2,2 +2,3 @@ router.post("/orders", async function submitOrder(req, res) {
+  validate(req.body)
"""

    symbols = extract_generic_backend_symbols_from_diff(diff, project_root=str(tmp_path), languages=["nodejs"])

    assert symbols[0].symbol == "submitOrder"
    assert symbols[0].symbol_type == "controller_action"


def test_detects_go_handler(tmp_path: Path):
    _write(tmp_path / "handlers" / "orders.go", """
func SubmitOrder(c *gin.Context) {
    service.Submit(c)
}
""")
    diff = """\
diff --git a/handlers/orders.go b/handlers/orders.go
--- a/handlers/orders.go
+++ b/handlers/orders.go
@@ -2,2 +2,3 @@ func SubmitOrder(c *gin.Context) {
+    validate(c)
"""

    symbols = extract_generic_backend_symbols_from_diff(diff, project_root=str(tmp_path), languages=["go"])

    assert symbols[0].symbol == "SubmitOrder"
    assert symbols[0].symbol_type == "controller_action"


def test_detects_php_laravel_controller_action(tmp_path: Path):
    _write(tmp_path / "app" / "Http" / "Controllers" / "OrderController.php", """
class OrderController {
    public function submit(Request $request) {
        return $this->service->submit($request);
    }
}
""")
    diff = """\
diff --git a/app/Http/Controllers/OrderController.php b/app/Http/Controllers/OrderController.php
--- a/app/Http/Controllers/OrderController.php
+++ b/app/Http/Controllers/OrderController.php
@@ -3,2 +3,3 @@ class OrderController {
+        $this->validate($request);
"""

    symbols = extract_generic_backend_symbols_from_diff(diff, project_root=str(tmp_path), languages=["php"])

    assert symbols[0].symbol == "submit"
    assert symbols[0].symbol_type == "controller_action"


def test_detects_cpp_service_function(tmp_path: Path):
    _write(tmp_path / "src" / "order_service.cpp", """
OrderResult SubmitOrder(const SubmitOrderRequest& request) {
    return repository.Save(request);
}
""")
    diff = """\
diff --git a/src/order_service.cpp b/src/order_service.cpp
--- a/src/order_service.cpp
+++ b/src/order_service.cpp
@@ -2,2 +2,3 @@ OrderResult SubmitOrder(const SubmitOrderRequest& request) {
+    Validate(request);
"""

    symbols = extract_generic_backend_symbols_from_diff(diff, project_root=str(tmp_path), languages=["cpp"])

    assert symbols[0].symbol == "SubmitOrder"
    assert symbols[0].symbol_type == "service_method"
