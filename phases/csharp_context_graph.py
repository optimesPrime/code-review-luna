# phases/csharp_context_graph.py
from __future__ import annotations
import json
import re
from pathlib import Path

from phases.backend_models import BackendContextGraph, BackendGraphEdge, BackendGraphNode


_SKIP_DIRS = {"bin", "obj", ".git", ".vs", "node_modules"}
_CLASS_RE = re.compile(r"\bclass\s+(\w+)")
_METHOD_RE = re.compile(
    r"^\s*(?:public|private|protected|internal)\s+"
    r"(?:async\s+)?(?:[\w<>\[\],?]+\s+)+(\w+)\s*\(([^)]*)\)"
)
_PROPERTY_RE = re.compile(r"^\s*public\s+([\w<>\[\],?]+)\s+(\w+)\s*\{\s*get;")
_ATTRIBUTE_RE = re.compile(r"^\s*\[(\w+)(?:\(([^]]*)\))?")
_FIELD_TYPE_RE = re.compile(r"private\s+readonly\s+(\w+)\s+(_\w+)")


def build_csharp_backend_graph(project_root: str) -> BackendContextGraph:
    root = Path(project_root)
    graph = BackendContextGraph()
    files = [
        p for p in root.rglob("*.cs")
        if not any(part in _SKIP_DIRS for part in p.relative_to(root).parts)
    ]

    method_index: dict[str, str] = {}
    field_type_index: dict[str, dict[str, str]] = {}

    # First pass: build nodes and method index
    for path in files:
        rel = str(path.relative_to(root))
        lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
        # MVP: only the first class per file
        class_name = _class_name(lines)
        if not class_name:
            continue
        field_type_index[rel] = _field_types(lines)

        for method in _methods(lines):
            node_id = f"{rel}:{class_name}.{method['name']}"
            node_type = _node_type(class_name, method["attributes"])
            graph.add_node(BackendGraphNode(
                id=node_id,
                node_type=node_type,
                file=rel,
                name=f"{class_name}.{method['name']}",
                line=method["line"],
                attributes=method["attributes"],
            ))
            method_index[f"{class_name}.{method['name']}"] = node_id
            method_index[method["name"]] = node_id

            for attr in method["attributes"]:
                if attr == "Authorize":
                    graph.add_edge(BackendGraphEdge(
                        source=node_id,
                        target=f"auth:{class_name}.{method['name']}",
                        edge_type="requires_auth",
                        evidence=f"{rel}:{method['line']} [{attr}]",
                        confidence="high",
                    ))
                if attr.startswith("Http"):
                    graph.add_edge(BackendGraphEdge(
                        source=node_id,
                        target=f"endpoint:{class_name}.{method['name']}",
                        edge_type="exposes_endpoint",
                        evidence=f"{rel}:{method['line']} [{attr}]",
                        confidence="high",
                    ))

        for prop in _properties(lines):
            node_id = f"{rel}:{class_name}.{prop['name']}"
            graph.add_node(BackendGraphNode(
                id=node_id,
                node_type=_property_node_type(rel, class_name),
                file=rel,
                name=f"{class_name}.{prop['name']}",
                line=prop["line"],
            ))

    # Second pass: resolve call edges
    for path in files:
        rel = str(path.relative_to(root))
        lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
        class_name = _class_name(lines)
        if not class_name:
            continue

        fields = field_type_index.get(rel, {})
        current_method = ""
        for i, line in enumerate(lines, 1):
            method = _METHOD_RE.match(line)
            if method:
                current_method = f"{rel}:{class_name}.{method.group(1)}"
                continue
            if not current_method:
                continue

            for field_name, type_name in fields.items():
                for call_match in re.finditer(rf"\b{re.escape(field_name)}\.(\w+)\s*\(", line):
                    target = method_index.get(f"{type_name}.{call_match.group(1)}")
                    if target:
                        graph.add_edge(BackendGraphEdge(
                            source=current_method,
                            target=target,
                            edge_type="calls",
                            evidence=f"{rel}:{i} {line.strip()}",
                            confidence="medium",
                        ))

            if "SaveChanges" in line or "SaveChangesAsync" in line:
                graph.add_edge(BackendGraphEdge(
                    source=current_method,
                    target=f"db:{rel}:{i}",
                    edge_type="writes_db",
                    evidence=f"{rel}:{i} {line.strip()}",
                    confidence="high",
                ))
            if "HttpClient" in line or ".SendAsync(" in line or ".GetAsync(" in line or ".PostAsync(" in line:
                graph.add_edge(BackendGraphEdge(
                    source=current_method,
                    target=f"external:{rel}:{i}",
                    edge_type="calls_external_api",
                    evidence=f"{rel}:{i} {line.strip()}",
                    confidence="medium",
                ))

    return graph


def save_backend_graph(graph: BackendContextGraph, path: str) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    data = {
        "nodes": {
            nid: {"id": n.id, "node_type": n.node_type, "file": n.file,
                  "name": n.name, "line": n.line, "attributes": n.attributes}
            for nid, n in graph.nodes.items()
        },
        "edges": [
            {"source": e.source, "target": e.target, "edge_type": e.edge_type,
             "evidence": e.evidence, "confidence": e.confidence}
            for e in graph.edges
        ],
    }
    Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def load_backend_graph(path: str) -> BackendContextGraph | None:
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


def _class_name(lines: list[str]) -> str:
    # MVP: returns the first class found. Files with multiple classes or
    # partial classes will have all methods attributed to the first class name.
    for line in lines:
        match = _CLASS_RE.search(line)
        if match:
            return match.group(1)
    return ""


def _field_types(lines: list[str]) -> dict[str, str]:
    fields: dict[str, str] = {}
    for line in lines:
        match = _FIELD_TYPE_RE.search(line)
        if match:
            fields[match.group(2)] = match.group(1)
    return fields


def _methods(lines: list[str]) -> list[dict]:
    methods: list[dict] = []
    attrs: list[str] = []
    for i, line in enumerate(lines, 1):
        attr = _ATTRIBUTE_RE.match(line.strip())
        if attr:
            attrs.append(attr.group(1))
            continue
        method = _METHOD_RE.match(line)
        if method:
            methods.append({"name": method.group(1), "line": i, "attributes": attrs})
            attrs = []
        elif line.strip():
            attrs = []
    return methods


def _properties(lines: list[str]) -> list[dict]:
    props: list[dict] = []
    for i, line in enumerate(lines, 1):
        match = _PROPERTY_RE.match(line)
        if match:
            props.append({"type": match.group(1), "name": match.group(2), "line": i})
    return props


def _node_type(class_name: str, attributes: list[str]) -> str:
    if class_name.endswith("Controller") or any(a.startswith("Http") for a in attributes):
        return "controller_action"
    if class_name.endswith("Service"):
        return "service_method"
    if class_name.endswith("Repository"):
        return "repository_method"
    return "method"


def _property_node_type(rel: str, class_name: str) -> str:
    lowered = f"{rel} {class_name}".lower()
    if "entity" in lowered:
        return "entity_property"
    if "model" in lowered or "dto" in lowered or "request" in lowered or "response" in lowered:
        return "model_property"
    return "property"
