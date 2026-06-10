# tests/test_backend_adapter_registry.py
from phases.backend_adapter_registry import detect_backend_languages_from_diff, should_run_backend_review, is_frontend_only_diff


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


# --- is_frontend_only_diff ---

def test_vue_project_is_frontend_only():
    diff = "\n".join([
        "diff --git a/src/components/Header.vue b/src/components/Header.vue",
        "diff --git a/src/store/user.ts b/src/store/user.ts",
        "diff --git a/src/router/index.ts b/src/router/index.ts",
    ])
    assert is_frontend_only_diff(diff) is True


def test_react_tsx_project_is_frontend_only():
    diff = "\n".join([
        "diff --git a/src/App.tsx b/src/App.tsx",
        "diff --git a/src/hooks/useAuth.ts b/src/hooks/useAuth.ts",
    ])
    assert is_frontend_only_diff(diff) is True


def test_jsx_project_is_frontend_only():
    diff = "\n".join([
        "diff --git a/src/Button.jsx b/src/Button.jsx",
        "diff --git a/src/utils.js b/src/utils.js",
    ])
    assert is_frontend_only_diff(diff) is True


def test_ts_only_diff_is_not_frontend_only():
    """Pure .ts/.js without .vue/.tsx/.jsx is ambiguous — could be Node.js backend."""
    diff = "\n".join([
        "diff --git a/src/server.ts b/src/server.ts",
        "diff --git a/src/routes/user.ts b/src/routes/user.ts",
    ])
    assert is_frontend_only_diff(diff) is False


def test_csharp_diff_is_not_frontend_only():
    diff = "diff --git a/Controllers/OrderController.cs b/Controllers/OrderController.cs"
    assert is_frontend_only_diff(diff) is False


def test_fullstack_vue_and_go_is_not_frontend_only():
    """Vue frontend + Go backend in same diff → not frontend-only."""
    diff = "\n".join([
        "diff --git a/frontend/src/App.vue b/frontend/src/App.vue",
        "diff --git a/backend/handler.go b/backend/handler.go",
    ])
    assert is_frontend_only_diff(diff) is False


def test_fullstack_vue_and_python_is_not_frontend_only():
    """Vue frontend + Python backend in same diff → not frontend-only."""
    diff = "\n".join([
        "diff --git a/frontend/App.vue b/frontend/App.vue",
        "diff --git a/api/views.py b/api/views.py",
    ])
    assert is_frontend_only_diff(diff) is False
