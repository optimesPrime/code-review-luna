# phases/context_graph.py
from __future__ import annotations
import re
from dataclasses import dataclass, field
from pathlib import Path


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


def build_graph(project_root: str) -> ContextGraph:
    root = Path(project_root)
    graph = ContextGraph()

    source_files = [
        p for p in root.rglob("*")
        if p.suffix in _SOURCE_EXTS
        and not any(part in _SKIP_DIRS for part in p.relative_to(root).parts)
    ]

    for src in source_files:
        rel = str(src.relative_to(root))
        graph.nodes[rel] = GraphNode(id=rel, node_type="file", file=rel, name=rel)

        try:
            content = src.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue

        for i, line in enumerate(content.splitlines(), 1):
            for pat, sym_type in _EXPORT_PATTERNS:
                m = re.match(pat, line)
                if m:
                    sym = m.group(1)
                    nid = f"{rel}:{sym}"
                    graph.nodes[nid] = GraphNode(
                        id=nid, node_type="export", file=rel, name=sym, line=i
                    )
                    graph.edges.append(GraphEdge(source=rel, target=nid, edge_type="exports"))

        for pat in _IMPORT_PATTERNS:
            for m in re.finditer(pat, content, re.MULTILINE):
                resolved = _resolve_import(src.parent, root, m.group(1))
                if resolved:
                    graph.edges.append(GraphEdge(source=rel, target=resolved, edge_type="imports"))
                    graph._importers.setdefault(resolved, set()).add(rel)

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
