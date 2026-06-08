from __future__ import annotations
import re
from dataclasses import dataclass, field
from pathlib import Path
from phases._vue_utils import extract_vue_script as _extract_vue_script

@dataclass
class DiffHunk:
    start_line: int
    line_count: int


@dataclass
class DiffFile:
    path: str
    hunks: list[DiffHunk] = field(default_factory=list)
    is_new_file: bool = False
    is_deleted: bool = False


@dataclass
class ChangedSymbol:
    file: str
    symbol: str
    symbol_type: str  # "function" | "class" | "component" | "export"
    start_line: int
    change_type: str  # "added" | "modified"


_SYMBOL_PATTERNS: list[tuple[str, str]] = [
    (r"^\s*(?:export\s+)?(?:async\s+)?function\s+(\w+)", "function"),
    (r"^\s*(?:export\s+)?const\s+(\w+)\s*=\s*(?:async\s+)?\(", "function"),
    (r"^\s*(?:export\s+)?const\s+(\w+)\s*=\s*(?:async\s+)?function", "function"),
    (r"^\s*(?:export\s+)?class\s+(\w+)", "class"),
    (r"^\s*(?:const|let)\s+(\w+)\s*=\s*defineComponent\(", "component"),
    (r"^\s*(?:const|let)\s+(\w+)\s*=\s*defineStore\(", "component"),
]


def locate_symbols(file_path: str, changed_lines: list[int]) -> list[ChangedSymbol]:
    path = Path(file_path)
    ext = path.suffix.lower()
    if ext not in {".js", ".ts", ".jsx", ".tsx", ".mjs", ".cjs", ".vue"}:
        return _locate_symbols_regex(file_path, changed_lines)
    try:
        return _locate_symbols_ast(path, changed_lines)
    except (ImportError, ModuleNotFoundError, OSError):
        return _locate_symbols_regex(file_path, changed_lines)


def _locate_symbols_regex(file_path: str, changed_lines: list[int]) -> list[ChangedSymbol]:
    """Fallback: original regex-based implementation."""
    try:
        with open(file_path, encoding="utf-8", errors="ignore") as f:
            source_lines = f.readlines()
    except OSError:
        return []
    if not source_lines:
        return []
    symbols: list[ChangedSymbol] = []
    seen: set[str] = set()
    for changed_line in changed_lines:
        search_from = min(changed_line - 1, len(source_lines) - 1)
        for i in range(search_from, -1, -1):
            line = source_lines[i]
            for pattern, sym_type in _SYMBOL_PATTERNS:
                m = re.search(pattern, line)
                if m:
                    symbol_name = m.group(1)
                    key = f"{file_path}:{symbol_name}"
                    if key not in seen:
                        seen.add(key)
                        symbols.append(ChangedSymbol(
                            file=file_path, symbol=symbol_name, symbol_type=sym_type,
                            start_line=i + 1, change_type="modified",
                        ))
                    break
            else:
                continue
            break
    return symbols


def _get_js_language(ext: str):
    if ext in (".ts", ".tsx"):
        import tree_sitter_typescript as tsts
        return tsts.language_tsx() if ext == ".tsx" else tsts.language_typescript()
    import tree_sitter_javascript as tsjs
    return tsjs.language()


def _locate_symbols_ast(path: Path, changed_lines: list[int]) -> list[ChangedSymbol]:
    from tree_sitter import Language, Parser

    ext = path.suffix.lower()
    line_offset = 0

    if ext == ".vue":
        source, line_offset = _extract_vue_script(path)
        if not source:
            return []
        lang = _get_js_language(".ts")
    else:
        try:
            source = path.read_bytes()
        except OSError:
            return []
        lang = _get_js_language(ext)

    parser = Parser(Language(lang))
    tree = parser.parse(source)
    root = tree.root_node
    src_lines = source.decode("utf-8", errors="ignore").split("\n")

    symbols: list[ChangedSymbol] = []
    seen: set[str] = set()

    for changed_line in changed_lines:
        script_line = changed_line - line_offset
        if script_line < 1 or script_line > len(src_lines):
            continue

        raw = src_lines[script_line - 1]
        col = len(raw) - len(raw.lstrip()) if raw.strip() else 0
        leaf = root.descendant_for_point_range((script_line - 1, col), (script_line - 1, col))
        if leaf is None:
            continue

        node = leaf
        while node is not None:
            if node.type in (
                "function_declaration", "arrow_function", "function",
                "method_definition", "class_declaration",
            ):
                break
            node = node.parent

        if node is None:
            continue

        name, sym_type = _classify_js_node(node, source)
        if not name:
            continue

        key = f"{path}:{name}"
        if key not in seen:
            seen.add(key)
            start_line = node.start_point[0] + 1 + line_offset
            symbols.append(ChangedSymbol(
                file=str(path), symbol=name, symbol_type=sym_type,
                start_line=start_line, change_type="modified",
            ))

    return symbols


def _classify_js_node(node, source: bytes) -> tuple[str, str]:
    name = _js_node_name(node, source)
    if not name:
        return ("", "")
    if node.type == "class_declaration":
        return (name, "class")
    if _is_call_wrapper(node, source, "defineStore"):
        return (name, "store")
    if _is_call_wrapper(node, source, "defineComponent"):
        return (name, "component")
    if name.startswith("use") and len(name) > 3 and (name[3].isupper() or name[3] == "_"):
        return (name, "hook")
    if name[0].isupper():
        return (name, "component")
    return (name, "function")


def _js_node_name(node, source: bytes) -> str:
    t = node.type
    if t in ("function_declaration", "class_declaration", "method_definition"):
        nn = node.child_by_field_name("name")
        return _sym_text(nn, source)
    if t in ("arrow_function", "function"):
        # Walk up to find the variable declarator that names this function
        cur = node.parent
        while cur is not None:
            if cur.type == "variable_declarator":
                nn = cur.child_by_field_name("name")
                return _sym_text(nn, source)
            if cur.type in ("call_expression", "arguments"):
                # Inside a defineStore/defineComponent call argument — keep walking
                cur = cur.parent
                continue
            break
    return ""


_FUNCTION_BODY_TYPES = frozenset({
    "function_declaration", "arrow_function", "function", "method_definition",
})


def _is_call_wrapper(node, source: bytes, callee: str) -> bool:
    """Check if this node is a *direct* argument of a callee(...) call expression.

    Stops traversal if we cross into another function body, so that functions
    *defined inside* a defineStore callback are not themselves classified as store.
    """
    cur = node.parent
    while cur is not None:
        if cur.type == "call_expression":
            fn = cur.child_by_field_name("function")
            if fn and _sym_text(fn, source) == callee:
                return True
        # Stop if we enter a new function scope that is not the node itself
        if cur.type in _FUNCTION_BODY_TYPES and cur is not node:
            return False
        cur = cur.parent
    return False


def _sym_text(node, source: bytes) -> str:
    if node is None:
        return ""
    return source[node.start_byte:node.end_byte].decode("utf-8", errors="ignore").strip()


def extract_changed_symbols_from_diff(
    diff: str,
    project_root: str = ".",
) -> list[ChangedSymbol]:
    root = Path(project_root)
    symbols: list[ChangedSymbol] = []

    for diff_file in parse_diff(diff):
        abs_path = root / diff_file.path
        if not abs_path.exists():
            continue

        changed_lines: list[int] = []
        for hunk in diff_file.hunks:
            for ln in range(hunk.start_line, hunk.start_line + hunk.line_count):
                changed_lines.append(ln)

        file_symbols = locate_symbols(str(abs_path), changed_lines)

        change_type = "added" if diff_file.is_new_file else "modified"
        for s in file_symbols:
            s.change_type = change_type
            s.file = diff_file.path  # normalize to project-relative path

        symbols.extend(file_symbols)

    return symbols


def parse_diff(diff: str) -> list[DiffFile]:
    files: list[DiffFile] = []
    current: DiffFile | None = None

    for line in diff.splitlines():
        if line.startswith("diff --git "):
            m = re.search(r" b/(.+)$", line)
            if m:
                current = DiffFile(path=m.group(1))
                files.append(current)
        elif line.startswith("new file mode") and current:
            current.is_new_file = True
        elif line.startswith("deleted file mode") and current:
            current.is_deleted = True
        elif line.startswith("@@ ") and current:
            # @@ -old_start,old_count +new_start,new_count @@
            m = re.search(r"\+(\d+)(?:,(\d+))?", line)
            if m:
                start = int(m.group(1))
                count = int(m.group(2)) if m.group(2) is not None else 1
                current.hunks.append(DiffHunk(start_line=start, line_count=count))

    return files
