# phases/adapters/__init__.py
from __future__ import annotations
from phases.backend_language_adapter import LanguageAdapter


def get_adapter(language: str) -> LanguageAdapter:
    """Return the adapter instance for the given language name."""
    language = language.lower().replace("node.js", "nodejs").replace("c++", "cpp")
    if language == "csharp":
        from phases.adapters.csharp_adapter import CSHARP_ADAPTER
        return CSHARP_ADAPTER
    raise ValueError(f"No adapter registered for language: {language!r}")
