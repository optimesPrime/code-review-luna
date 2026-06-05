# phases/backend_adapter_registry.py
from __future__ import annotations
import re

from phases.backend_language_profiles import get_profile, supported_languages


def detect_backend_languages_from_diff(diff: str) -> list[str]:
    extensions = set(re.findall(r" b/[^ \n]+(\.[A-Za-z0-9]+)", diff))
    detected: list[str] = []
    for language in supported_languages():
        profile = get_profile(language)
        if any(ext in extensions for ext in profile.extensions):
            detected.append(language)
    return detected


def should_run_backend_review(
    diff: str,
    project_type: str,
    languages: list[str],
) -> bool:
    if project_type not in ("backend", "fullstack"):
        return False
    detected = detect_backend_languages_from_diff(diff)
    enabled = {lang.lower().replace("node.js", "nodejs").replace("c++", "cpp") for lang in languages}
    return any(language in enabled for language in detected)


def get_adapter(language: str):
    """Return the LanguageAdapter instance for the given language name.

    Raises ValueError if no adapter is registered for that language.
    Plans B and C add adapters for java, python, nodejs, go, php, cpp.
    """
    from phases.adapters import get_adapter as _get
    return _get(language)
