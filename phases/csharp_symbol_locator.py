# phases/csharp_symbol_locator.py
from __future__ import annotations
import re
from pathlib import Path

from phases.backend_models import BackendChangedSymbol
from phases.symbol_locator import parse_diff


_CLASS_RE = re.compile(r"\b(?:public|internal|private|protected)?\s*(?:partial\s+)?class\s+(\w+)")
_METHOD_RE = re.compile(
    r"^\s*(?:public|private|protected|internal)\s+"
    r"(?:async\s+)?(?:[\w<>\[\],?]+\s+)+(\w+)\s*\([^;{}]*\)\s*$"
)
_PROPERTY_RE = re.compile(
    r"^\s*(?:public|private|protected|internal)\s+[\w<>\[\],?]+\s+(\w+)\s*\{\s*get;"
)
_ATTRIBUTE_RE = re.compile(r"^\s*\[(\w+)")


def extract_csharp_changed_symbols_from_diff(
    diff: str,
    project_root: str = ".",
) -> list[BackendChangedSymbol]:
    root = Path(project_root)
    symbols: list[BackendChangedSymbol] = []
    seen: set[str] = set()

    for diff_file in parse_diff(diff):
        if not diff_file.path.endswith(".cs") or diff_file.is_deleted:
            continue
        path = root / diff_file.path
        if not path.exists():
            continue

        lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
        changed_lines = [
            line
            for hunk in diff_file.hunks
            for line in range(hunk.start_line, hunk.start_line + hunk.line_count)
        ]

        for line_no in changed_lines:
            symbol = _locate_symbol(lines, line_no, diff_file.path, diff_file.is_new_file)
            if symbol and symbol.node_id not in seen:
                seen.add(symbol.node_id)
                symbols.append(symbol)

    return symbols


def _locate_symbol(
    lines: list[str],
    changed_line: int,
    rel_path: str,
    is_new_file: bool,
) -> BackendChangedSymbol | None:
    index = max(0, min(changed_line - 1, len(lines) - 1))
    class_name = _find_enclosing_class(lines, index)
    if not class_name:
        return None

    attributes = _collect_nearest_attributes(lines, index)

    for i in range(index, -1, -1):
        line = lines[i]
        prop = _PROPERTY_RE.search(line)
        if prop:
            name = prop.group(1)
            return BackendChangedSymbol(
                file=rel_path,
                symbol=name,
                symbol_type=_classify_property(rel_path, class_name),
                class_name=class_name,
                start_line=i + 1,
                change_type="added" if is_new_file else "modified",
                attributes=attributes,
                evidence=f"{rel_path}:{i + 1} {line.strip()}",
            )

        method = _METHOD_RE.search(line)
        if method:
            name = method.group(1)
            attrs = _collect_nearest_attributes(lines, i)
            return BackendChangedSymbol(
                file=rel_path,
                symbol=name,
                symbol_type=_classify_method(rel_path, class_name, attrs),
                class_name=class_name,
                start_line=i + 1,
                change_type="added" if is_new_file else "modified",
                attributes=attrs,
                evidence=f"{rel_path}:{i + 1} {line.strip()}",
            )

    return BackendChangedSymbol(
        file=rel_path,
        symbol=class_name,
        symbol_type=_classify_class(rel_path, class_name),
        class_name=class_name,
        start_line=index + 1,
        change_type="added" if is_new_file else "modified",
        attributes=attributes,
        evidence=f"{rel_path}:{index + 1} {lines[index].strip()}",
    )


def _find_enclosing_class(lines: list[str], index: int) -> str:
    # MVP: returns the first class found. Files with multiple classes or
    # partial classes will have all methods attributed to the first class name.
    for i in range(index, -1, -1):
        match = _CLASS_RE.search(lines[i])
        if match:
            return match.group(1)
    return ""


def _collect_nearest_attributes(lines: list[str], index: int) -> list[str]:
    attrs: list[str] = []
    i = index - 1
    while i >= 0:
        stripped = lines[i].strip()
        if not stripped:
            i -= 1
            continue
        match = _ATTRIBUTE_RE.match(stripped)
        if match:
            attrs.insert(0, match.group(1))
            i -= 1
            continue
        break
    return attrs


def _classify_method(rel_path: str, class_name: str, attributes: list[str]) -> str:
    if class_name.endswith("Controller") or any(a.startswith("Http") for a in attributes):
        return "controller_action"
    if "Service" in class_name:
        return "service_method"
    if "Repository" in class_name:
        return "repository_method"
    return "method"


def _classify_property(rel_path: str, class_name: str) -> str:
    lower = f"{rel_path} {class_name}".lower()
    if "model" in lower or "dto" in lower or "request" in lower or "response" in lower:
        return "model_property"
    if "entity" in lower:
        return "entity_property"
    return "property"


def _classify_class(rel_path: str, class_name: str) -> str:
    if class_name.endswith("Controller"):
        return "controller"
    if class_name.endswith("Service"):
        return "service"
    if class_name.endswith("Repository"):
        return "repository"
    if "Model" in rel_path or "Dto" in rel_path:
        return "model"
    return "class"
