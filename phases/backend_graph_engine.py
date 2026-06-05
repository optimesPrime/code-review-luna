# phases/backend_graph_engine.py
from __future__ import annotations
import json
from pathlib import Path
from typing import Any

from phases.backend_language_adapter import LanguageAdapter
from phases.backend_models import (
    BackendChangedSymbol,
    BackendContextGraph,
    BackendGraphEdge,
    BackendGraphNode,
)
from phases.symbol_locator import parse_diff

_SKIP_DIRS = {"bin", "obj", ".git", ".vs", "node_modules", "dist", "build", "__pycache__", ".luna"}


def find_symbols_from_diff(
    diff: str,
    adapter: LanguageAdapter,
    project_root: str = ".",
) -> list[BackendChangedSymbol]:
    root = Path(project_root)
    symbols: list[BackendChangedSymbol] = []
    seen: set[str] = set()

    for diff_file in parse_diff(diff):
        if not any(diff_file.path.endswith(ext) for ext in adapter.extensions):
            continue
        if diff_file.is_deleted:
            continue
        abs_path = root / diff_file.path
        if not abs_path.exists():
            continue

        root_node, source = _parse_file(abs_path, adapter)

        changed_lines = [
            ln
            for hunk in diff_file.hunks
            for ln in range(hunk.start_line, hunk.start_line + hunk.line_count)
        ]

        for line_no in changed_lines:
            symbol = adapter.find_enclosing_symbol(
                root_node, source, line_no, diff_file.path, diff_file.is_new_file
            )
            if symbol and symbol.node_id not in seen:
                seen.add(symbol.node_id)
                symbols.append(symbol)

    return symbols


def build_graph(
    adapter: LanguageAdapter,
    project_root: str = ".",
) -> BackendContextGraph:
    root = Path(project_root)
    graph = BackendContextGraph()
    method_index: dict[str, str] = {}

    files = [
        p for p in root.rglob("*")
        if p.is_file()
        and any(p.suffix == ext for ext in adapter.extensions)
        and not any(part in _SKIP_DIRS for part in p.relative_to(root).parts)
    ]

    for path in files:
        rel = str(path.relative_to(root))
        try:
            root_node, source = _parse_file(path, adapter)
        except OSError:
            continue
        nodes = adapter.extract_file_nodes(root_node, source, rel)
        for node in nodes:
            graph.add_node(node)
            method_index[node.name] = node.id
            short = node.name.split(".")[-1]
            method_index.setdefault(short, node.id)

    for path in files:
        rel = str(path.relative_to(root))
        try:
            root_node, source = _parse_file(path, adapter)
        except OSError:
            continue
        edges = adapter.extract_file_edges(root_node, source, rel, method_index)
        for edge in edges:
            graph.add_edge(edge)

    return graph


def save_graph(graph: BackendContextGraph, path: str) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    data = {
        "nodes": {
            nid: {
                "id": n.id, "node_type": n.node_type, "file": n.file,
                "name": n.name, "line": n.line, "attributes": n.attributes,
            }
            for nid, n in graph.nodes.items()
        },
        "edges": [
            {
                "source": e.source, "target": e.target, "edge_type": e.edge_type,
                "evidence": e.evidence, "confidence": e.confidence,
            }
            for e in graph.edges
        ],
    }
    Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def load_graph(path: str) -> BackendContextGraph | None:
    p = Path(path)
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    graph = BackendContextGraph()
    for nid, n in data.get("nodes", {}).items():
        graph.add_node(BackendGraphNode(
            id=n["id"], node_type=n["node_type"], file=n["file"],
            name=n["name"], line=n.get("line", 0), attributes=n.get("attributes", []),
        ))
    for e in data.get("edges", []):
        graph.add_edge(BackendGraphEdge(
            source=e["source"], target=e["target"], edge_type=e["edge_type"],
            evidence=e["evidence"], confidence=e.get("confidence", "high"),
        ))
    return graph


def _parse_file(path: Path, adapter: LanguageAdapter) -> tuple[Any, bytes]:
    from tree_sitter import Language, Parser
    source = path.read_bytes()
    lang = Language(adapter.get_language())
    parser = Parser(lang)
    tree = parser.parse(source)
    return tree.root_node, source
