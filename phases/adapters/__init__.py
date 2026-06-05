# phases/adapters/__init__.py
from __future__ import annotations
from phases.backend_language_adapter import LanguageAdapter


def get_adapter(language: str) -> LanguageAdapter:
    """Return the adapter instance for the given language name."""
    language = language.lower().replace("node.js", "nodejs").replace("c++", "cpp")
    if language == "csharp":
        from phases.adapters.csharp_adapter import CSHARP_ADAPTER
        return CSHARP_ADAPTER
    if language == "java":
        from phases.adapters.java_adapter import JAVA_ADAPTER
        return JAVA_ADAPTER
    if language == "python":
        from phases.adapters.python_adapter import PYTHON_ADAPTER
        return PYTHON_ADAPTER
    if language == "nodejs":
        from phases.adapters.nodejs_adapter import NODEJS_ADAPTER
        return NODEJS_ADAPTER
    if language == "go":
        from phases.adapters.go_adapter import GOLANG_ADAPTER
        return GOLANG_ADAPTER
    if language == "php":
        from phases.adapters.php_adapter import PHP_ADAPTER
        return PHP_ADAPTER
    if language == "cpp":
        from phases.adapters.cpp_adapter import CPP_ADAPTER
        return CPP_ADAPTER
    raise ValueError(f"No adapter registered for language: {language!r}")
