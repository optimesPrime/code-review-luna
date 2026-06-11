# phases/context_graph.py
from __future__ import annotations
import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from phases._vue_utils import extract_vue_script as _extract_vue_script


@dataclass
class GraphNode:
    id: str
    node_type: str  # "file" | "export"
    file: str
    name: str
    line: int = 0


@dataclass
class GraphEdge:
    source: str
    target: str
    edge_type: str  # "imports" | "exports"


@dataclass
class ContextGraph:
    nodes: dict[str, GraphNode] = field(default_factory=dict)
    edges: list[GraphEdge] = field(default_factory=list)
    # _importers[file] = set of files that import it
    _importers: dict[str, set[str]] = field(default_factory=dict)

    def find_usages(self, file_path: str) -> list[str]:
        return list(self._importers.get(file_path, set()))


_IMPORT_PATTERNS = [
    r"""import\s+(?:[^'"]+\s+from\s+)?['"](\.\.?/[^'"]+)['"]""",
    r"""require\s*\(\s*['"](\.\.?/[^'"]+)['"]\s*\)""",
]

_EXPORT_PATTERNS = [
    (r"^export\s+(?:async\s+)?function\s+(\w+)", "function"),
    (r"^export\s+const\s+(\w+)\s*=", "export"),
    (r"^export\s+class\s+(\w+)", "class"),
]

_SKIP_DIRS = {"node_modules", ".git", "dist", "build", "__pycache__", ".cache", ".luna"}
_SOURCE_EXTS = {".js", ".ts", ".jsx", ".tsx", ".vue"}


def _scan_source_files(root: Path) -> list[Path]:
    """Return resolved absolute paths of all source files under root."""
    result: list[Path] = []
    for p in root.rglob("*"):
        if not p.is_file() or p.suffix not in _SOURCE_EXTS:
            continue
        try:
            resolved = p.resolve()
            if not any(part in _SKIP_DIRS for part in resolved.relative_to(root).parts):
                result.append(resolved)
        except (ValueError, OSError):
            continue
    return result


def _parse_file(src: Path, rel: str, root: Path, graph: "ContextGraph") -> None:
    try:
        if src.suffix == ".vue":
            _process_vue_file(src, rel, root, graph)
        else:
            _process_js_file(src, rel, root, graph)
    except (ImportError, ModuleNotFoundError, OSError, ValueError, RecursionError):
        _process_file_regex(src, rel, root, graph)


def build_graph(project_root: str) -> ContextGraph:
    root = Path(project_root).resolve()
    graph = ContextGraph()

    # ── SQLite incremental cache ──────────────────────────────────────────────
    db = None
    cached_hashes: dict[str, str] = {}
    try:
        from phases.sqlite_graph import GraphDB
        _db_dir = root / ".luna" / "cache"
        _db_dir.mkdir(parents=True, exist_ok=True)
        db = GraphDB(str(_db_dir / "context-graph.db"))
        cached_hashes = db.get_all_hashes()
    except Exception:
        db = None  # cache unavailable — proceed with full parse

    source_files = _scan_source_files(root)

    # Track which files were skipped (cache hit) vs re-parsed
    reparsed: set[str] = set()
    skipped: set[str] = set()

    for src in source_files:
        rel = str(src.relative_to(root))
        graph.nodes[rel] = GraphNode(id=rel, node_type="file", file=rel, name=rel)

        try:
            content_bytes = src.read_bytes()
        except OSError:
            continue
        new_hash = hashlib.sha256(content_bytes).hexdigest()

        if db is not None and cached_hashes.get(str(src)) == new_hash:
            skipped.add(str(src))
            # Safe to skip: luna's pipeline only uses graph._importers (for BFS)
            # and graph.edges (for degree calc). Symbol nodes in graph.nodes are
            # not consumed by any downstream caller after build_graph() returns.
            continue

        reparsed.add(str(src))
        _parse_file(src, rel, root, graph)

    # ── Sync SQLite and merge cached edges ────────────────────────────────────
    if db is not None:
        db_read_ok = False
        try:
            current_abs = {str(p) for p in source_files}
            deleted_abs = set(db.get_all_files()) - current_abs

            # Use public API — no direct _conn access
            db.sync(
                deleted=deleted_abs,
                reparsed=reparsed,
                new_importers=graph._importers,
                project_root=str(root),
            )

            # Merge cached edges for skipped (unchanged) files
            for imported_abs, importer_abs in db.get_all_edges():
                try:
                    imported_rel = str(Path(imported_abs).relative_to(root))
                    importer_rel = str(Path(importer_abs).relative_to(root))
                except ValueError:
                    continue
                graph._importers.setdefault(imported_rel, set()).add(importer_rel)

            db_read_ok = True

        except Exception as _e:
            import sys
            print(f"[luna] SQLite cache error ({_e}), continuing without cache.", file=sys.stderr)
        finally:
            db.close()

        # If we skipped files but couldn't load their cached edges, re-parse them
        if not db_read_ok and skipped:
            for abs_path in skipped:
                src = Path(abs_path)
                rel = str(src.relative_to(root))
                _parse_file(src, rel, root, graph)

    # Rebuild graph.edges from merged _importers
    # Convention: source=importer(a.ts), target=imported(b.ts)  ("a imports b")
    graph.edges = [
        GraphEdge(source=importer_rel, target=imported_rel, edge_type="imports")
        for imported_rel, importers in graph._importers.items()
        for importer_rel in importers
    ]

    return graph


def _get_graph_parser(ext: str):
    from tree_sitter import Language, Parser
    if ext in (".ts", ".tsx"):
        import tree_sitter_typescript as tsts
        lang = tsts.language_tsx() if ext == ".tsx" else tsts.language_typescript()
    else:
        import tree_sitter_javascript as tsjs
        lang = tsjs.language()
    return Parser(Language(lang))


def _process_js_file(src: Path, rel: str, root: Path, graph: ContextGraph) -> None:
    source = src.read_bytes()
    parser = _get_graph_parser(src.suffix.lower())
    tree = parser.parse(source)
    _extract_graph_exports(tree.root_node, source, rel, graph, line_offset=0)
    _extract_graph_imports(tree.root_node, source, src, rel, root, graph)


def _process_vue_file(src: Path, rel: str, root: Path, graph: ContextGraph) -> None:
    script, line_offset = _extract_vue_script(src)
    if not script:
        return
    parser = _get_graph_parser(".ts")
    tree = parser.parse(script)
    _extract_graph_exports(tree.root_node, script, rel, graph, line_offset=line_offset)
    _extract_graph_imports(tree.root_node, script, src, rel, root, graph)


def _extract_graph_exports(root_node, source: bytes, rel: str, graph: ContextGraph, line_offset: int) -> None:
    stack = [root_node]
    while stack:
        node = stack.pop()
        if node.type == "export_statement":
            _handle_graph_export(node, source, rel, graph, line_offset)
            continue  # Don't descend into export nodes
        # Top-level defineStore/defineComponent without export keyword
        if node.type in ("lexical_declaration", "variable_declaration"):
            parent = node.parent
            if parent and parent.type in ("program", "module"):
                _handle_top_level_store_or_component(node, source, rel, graph, line_offset)
        stack.extend(node.children)


def _handle_graph_export(node, source: bytes, rel: str, graph: ContextGraph, line_offset: int) -> None:
    for child in node.children:
        name, sym_type = "", ""
        if child.type == "function_declaration":
            nn = child.child_by_field_name("name")
            name = _gtext(nn, source)
            sym_type = _classify_export_sym(name, child, source)
        elif child.type == "class_declaration":
            nn = child.child_by_field_name("name")
            name = _gtext(nn, source)
            sym_type = "class"
        elif child.type in ("lexical_declaration", "variable_declaration"):
            for decl in child.children:
                if decl.type == "variable_declarator":
                    nn = decl.child_by_field_name("name")
                    name = _gtext(nn, source)
                    val = decl.child_by_field_name("value")
                    sym_type = _classify_export_sym(name, val, source) if val else _classify_export_sym(name, child, source)
                    break
        if name:
            nid = f"{rel}:{name}"
            line = node.start_point[0] + 1 + line_offset
            graph.nodes[nid] = GraphNode(id=nid, node_type=sym_type or "export", file=rel, name=name, line=line)
            graph.edges.append(GraphEdge(source=rel, target=nid, edge_type="exports"))


def _handle_top_level_store_or_component(node, source: bytes, rel: str, graph: ContextGraph, line_offset: int) -> None:
    for child in node.children:
        if child.type != "variable_declarator":
            continue
        nn = child.child_by_field_name("name")
        name = _gtext(nn, source)
        val = child.child_by_field_name("value")
        if val and val.type == "call_expression":
            fn = val.child_by_field_name("function")
            fn_name = _gtext(fn, source)
            if fn_name in ("defineStore", "defineComponent"):
                sym_type = "store" if fn_name == "defineStore" else "component"
                nid = f"{rel}:{name}"
                if nid not in graph.nodes:
                    line = node.start_point[0] + 1 + line_offset
                    graph.nodes[nid] = GraphNode(id=nid, node_type=sym_type, file=rel, name=name, line=line)
                    graph.edges.append(GraphEdge(source=rel, target=nid, edge_type="exports"))


def _extract_graph_imports(root_node, source: bytes, src: Path, rel: str, root: Path, graph: ContextGraph) -> None:
    stack = [root_node]
    while stack:
        node = stack.pop()
        if node.type == "import_statement":
            for child in node.children:
                if child.type == "string":
                    raw = _gtext(child, source).strip("'\"")
                    if raw.startswith("."):
                        resolved = _resolve_import(src.parent, root, raw)
                        if resolved:
                            graph.edges.append(GraphEdge(source=rel, target=resolved, edge_type="imports"))
                            graph._importers.setdefault(resolved, set()).add(rel)
                    break
        else:
            stack.extend(node.children)


def _classify_export_sym(name: str, node, source: bytes) -> str:
    if not name:
        return "export"
    # Check for defineStore / defineComponent call
    if node is not None and node.type == "call_expression":
        fn = node.child_by_field_name("function")
        fn_name = _gtext(fn, source)
        if fn_name == "defineStore":
            return "store"
        if fn_name == "defineComponent":
            return "component"
    if node is not None and node.type == "class_declaration":
        return "class"
    if name.startswith("use") and len(name) > 3 and (name[3].isupper() or name[3] == "_"):
        return "hook"
    if name[0].isupper():
        return "component"
    return "function"


def _process_file_regex(src: Path, rel: str, root: Path, graph: ContextGraph) -> None:
    """Fallback: original regex-based processing."""
    try:
        content = src.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return
    for i, line in enumerate(content.splitlines(), 1):
        for pat, sym_type in _EXPORT_PATTERNS:
            m = re.match(pat, line)
            if m:
                sym = m.group(1)
                nid = f"{rel}:{sym}"
                graph.nodes[nid] = GraphNode(id=nid, node_type="export", file=rel, name=sym, line=i)
                graph.edges.append(GraphEdge(source=rel, target=nid, edge_type="exports"))
    for pat in _IMPORT_PATTERNS:
        for m in re.finditer(pat, content, re.MULTILINE):
            resolved = _resolve_import(src.parent, root, m.group(1))
            if resolved:
                graph.edges.append(GraphEdge(source=rel, target=resolved, edge_type="imports"))
                graph._importers.setdefault(resolved, set()).add(rel)


def _gtext(node, source: bytes) -> str:
    if node is None:
        return ""
    return source[node.start_byte:node.end_byte].decode("utf-8", errors="ignore").strip()


def save_graph(graph: ContextGraph, path: str) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    data = {
        "nodes": {
            nid: {"id": n.id, "node_type": n.node_type, "file": n.file,
                  "name": n.name, "line": n.line}
            for nid, n in graph.nodes.items()
        },
        "edges": [
            {"source": e.source, "target": e.target, "edge_type": e.edge_type}
            for e in graph.edges
        ],
        "importers": {k: list(v) for k, v in graph._importers.items()},
    }
    Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def load_graph(path: str) -> ContextGraph | None:
    p = Path(path)
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None

    graph = ContextGraph()
    for nid, n in data.get("nodes", {}).items():
        graph.nodes[nid] = GraphNode(
            id=n["id"], node_type=n["node_type"],
            file=n["file"], name=n["name"], line=n.get("line", 0),
        )
    for e in data.get("edges", []):
        graph.edges.append(GraphEdge(
            source=e["source"], target=e["target"], edge_type=e["edge_type"]
        ))
    for k, v in data.get("importers", {}).items():
        graph._importers[k] = set(v)

    return graph


def _resolve_import(current_dir: Path, root: Path, import_path: str) -> str | None:
    """Resolve a relative import path to a project-relative file path."""
    resolved_root = root.resolve()
    candidate = (current_dir / import_path).resolve()
    for ext in _SOURCE_EXTS:
        p = candidate.with_suffix(ext)
        if p.exists():
            try:
                return str(p.relative_to(resolved_root))
            except ValueError:
                return None
    if candidate.exists():
        try:
            return str(candidate.relative_to(resolved_root))
        except ValueError:
            return None
    for ext in _SOURCE_EXTS:
        index = candidate / f"index{ext}"
        if index.exists():
            try:
                return str(index.relative_to(resolved_root))
            except ValueError:
                return None
    return None
