# phases/backend_generic_symbol_locator.py
from __future__ import annotations
import re
from pathlib import Path

from phases.backend_language_profiles import get_profile
from phases.backend_models import BackendChangedSymbol
from phases.symbol_locator import parse_diff


_PATTERNS = {
    "java": [
        (re.compile(r"^\s*public\s+[\w<>\[\]]+\s+(\w+)\s*\("), "method"),
    ],
    "python": [
        (re.compile(r"^\s*(?:async\s+)?def\s+(\w+)\s*\("), "method"),
    ],
    "nodejs": [
        (re.compile(r"function\s+(\w+)\s*\("), "method"),
        (re.compile(r"(?:const|let)\s+(\w+)\s*=\s*async\s*\("), "method"),
        (re.compile(r"(?:const|let)\s+(\w+)\s*=\s*\("), "method"),
    ],
    "go": [
        (re.compile(r"^\s*func\s+(\w+)\s*\("), "method"),
        (re.compile(r"^\s*func\s+\([^)]*\)\s+(\w+)\s*\("), "method"),
    ],
    "php": [
        (re.compile(r"^\s*public\s+function\s+(\w+)\s*\("), "method"),
        (re.compile(r"^\s*function\s+(\w+)\s*\("), "method"),
    ],
    "cpp": [
        (re.compile(r"^\s*[\w:<>,*&\s]+\s+(\w+)\s*\([^;]*\)\s*\{?\s*$"), "method"),
    ],
}


def extract_generic_backend_symbols_from_diff(
    diff: str,
    project_root: str = ".",
    languages: list[str] | None = None,
) -> list[BackendChangedSymbol]:
    root = Path(project_root)
    enabled = {normalize_language(lang) for lang in (languages or list(_PATTERNS))}
    symbols: list[BackendChangedSymbol] = []
    seen: set[str] = set()

    for diff_file in parse_diff(diff):
        language = _language_for_path(diff_file.path)
        if not language or language not in enabled or language == "csharp":
            continue
        path = root / diff_file.path
        if not path.exists() or diff_file.is_deleted:
            continue
        lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
        changed_lines = [
            line
            for hunk in diff_file.hunks
            for line in range(hunk.start_line, hunk.start_line + hunk.line_count)
        ]
        for line_no in changed_lines:
            symbol = _locate(lines, line_no, diff_file.path, language, diff_file.is_new_file)
            if symbol and symbol.node_id not in seen:
                seen.add(symbol.node_id)
                symbols.append(symbol)

    return symbols


def normalize_language(language: str) -> str:
    return language.lower().replace("node.js", "nodejs").replace("c++", "cpp")


def _language_for_path(path: str) -> str:
    suffix = Path(path).suffix
    # Check unambiguous extensions first; nodejs (.js/.ts) is last because
    # those extensions overlap with frontend — the CLI-level project_type
    # gate in should_run_backend_review() is what makes the final call.
    for language in ("java", "python", "go", "php", "cpp", "nodejs"):
        profile = get_profile(language)
        if suffix in profile.extensions:
            return language
    return ""


def _locate(
    lines: list[str],
    changed_line: int,
    rel_path: str,
    language: str,
    is_new_file: bool,
) -> BackendChangedSymbol | None:
    index = max(0, min(changed_line - 1, len(lines) - 1))
    class_name = _nearest_class(lines, index, language)
    for i in range(index, -1, -1):
        for pattern, _kind in _PATTERNS[language]:
            match = pattern.search(lines[i])
            if not match:
                continue
            name = match.group(1)
            attrs = _nearest_annotations(lines, i, language)
            symbol_type = _classify_symbol(rel_path, language, name, class_name, attrs, lines[i])
            return BackendChangedSymbol(
                file=rel_path,
                symbol=name,
                symbol_type=symbol_type,
                class_name=class_name or _module_name(rel_path),
                start_line=i + 1,
                change_type="added" if is_new_file else "modified",
                attributes=attrs,
                evidence=f"{rel_path}:{i + 1} {lines[i].strip()}",
            )
    return None


def _nearest_class(lines: list[str], index: int, language: str) -> str:
    class_patterns = {
        "java": re.compile(r"\bclass\s+(\w+)"),
        "python": re.compile(r"^\s*class\s+(\w+)"),
        "nodejs": re.compile(r"\bclass\s+(\w+)"),
        "go": re.compile(r"^\s*type\s+(\w+)\s+struct"),
        "php": re.compile(r"\bclass\s+(\w+)"),
        "cpp": re.compile(r"\bclass\s+(\w+)"),
    }
    pattern = class_patterns[language]
    for i in range(index, -1, -1):
        match = pattern.search(lines[i])
        if match:
            return match.group(1)
    return ""


def _nearest_annotations(lines: list[str], index: int, language: str) -> list[str]:
    attrs: list[str] = []
    for i in range(index - 1, max(-1, index - 8), -1):
        stripped = lines[i].strip()
        if not stripped:
            continue
        if language in ("java", "python") and stripped.startswith("@"):
            attrs.insert(0, stripped.split("(", 1)[0])
            continue
        if language == "php" and (stripped.startswith("#[") or stripped.startswith("@")):
            attrs.insert(0, stripped)
            continue
        break
    return attrs


def _classify_symbol(
    rel_path: str,
    language: str,
    name: str,
    class_name: str,
    attrs: list[str],
    source_line: str = "",
) -> str:
    if language == "cpp":
        return "service_method"
    lowered = f"{rel_path} {class_name} {name} {' '.join(attrs)} {source_line}".lower()
    if any(token in lowered for token in ("controller", "route", "handler", "mapping", "router.", "gin.context", "request")):
        return "controller_action"
    if any(token in lowered for token in ("repository", "dao", "store")):
        return "repository_method"
    if any(token in lowered for token in ("service", "usecase", "manager")):
        return "service_method"
    return "method"


def _module_name(rel_path: str) -> str:
    return Path(rel_path).stem
