# tests/test_backend_adapter_registry.py
from phases.backend_adapter_registry import detect_backend_languages_from_diff, should_run_backend_review


def test_detects_csharp_java_python_nodejs_go_php_cpp_from_diff():
    diff = "\n".join([
        "diff --git a/Foo.cs b/Foo.cs",
        "diff --git a/UserController.java b/UserController.java",
        "diff --git a/app.py b/app.py",
        "diff --git a/server.ts b/server.ts",
        "diff --git a/main.go b/main.go",
        "diff --git a/OrderController.php b/OrderController.php",
        "diff --git a/service.cpp b/service.cpp",
    ])

    languages = detect_backend_languages_from_diff(diff)

    assert set(languages) == {"csharp", "java", "python", "nodejs", "go", "php", "cpp"}


def test_should_run_backend_review_for_configured_language():
    diff = "diff --git a/Controllers/OrderController.cs b/Controllers/OrderController.cs"

    assert should_run_backend_review(diff, project_type="backend", languages=["csharp"]) is True
    assert should_run_backend_review(diff, project_type="frontend", languages=["csharp"]) is False
    assert should_run_backend_review(diff, project_type="backend", languages=["java"]) is False
