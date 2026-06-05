# phases/adapters/csharp_adapter.py
from __future__ import annotations
import re
from typing import Any

from phases.backend_models import BackendChangedSymbol, BackendGraphEdge, BackendGraphNode


class CSharpAdapter:
    name = "csharp"
    extensions = (".cs",)

    def get_language(self) -> Any:
        import tree_sitter_c_sharp as tscsharp
        return tscsharp.language()

    def find_enclosing_symbol(
        self,
        root_node: Any,
        source: bytes,
        line: int,
        rel_path: str,
        is_new_file: bool,
    ) -> BackendChangedSymbol | None:
        target_line = line - 1  # tree-sitter is 0-based

        # Use first non-whitespace column so indented members (e.g. properties)
        # are found correctly; col 0 only hits the parent declaration_list.
        lines = source.decode("utf-8", errors="ignore").split("\n")
        raw_line = lines[target_line] if target_line < len(lines) else ""
        col = len(raw_line) - len(raw_line.lstrip())
        if col >= len(raw_line):  # blank / whitespace-only line
            col = 0
        point = (target_line, col)
        leaf = root_node.descendant_for_point_range(point, point)
        if leaf is None:
            return None

        enclosing = None
        node = leaf
        while node is not None:
            if node.type in ("method_declaration", "property_declaration", "constructor_declaration"):
                enclosing = node
                break
            node = node.parent

        if enclosing is None:
            return None

        class_name = _enclosing_class_name(enclosing, source)
        if not class_name:
            return None

        name_node = enclosing.child_by_field_name("name")
        if name_node is None:
            return None
        method_name = _text(name_node, source)

        attrs = _collect_attributes(enclosing, source)
        sym_type = (
            _classify_method(class_name, attrs)
            if enclosing.type != "property_declaration"
            else _classify_property(rel_path, class_name)
        )

        return BackendChangedSymbol(
            file=rel_path,
            symbol=method_name,
            symbol_type=sym_type,
            class_name=class_name,
            start_line=enclosing.start_point[0] + 1,
            change_type="added" if is_new_file else "modified",
            attributes=attrs,
            evidence=f"{rel_path}:{enclosing.start_point[0] + 1} {_first_line(enclosing, source)}",
        )

    def extract_file_nodes(
        self,
        root_node: Any,
        source: bytes,
        rel_path: str,
    ) -> list[BackendGraphNode]:
        nodes: list[BackendGraphNode] = []
        for class_node in _find_all(root_node, "class_declaration"):
            class_name_node = class_node.child_by_field_name("name")
            if class_name_node is None:
                continue
            class_name = _text(class_name_node, source)

            for method_node in _find_all(class_node, "method_declaration"):
                name_node = method_node.child_by_field_name("name")
                if name_node is None:
                    continue
                method_name = _text(name_node, source)
                attrs = _collect_attributes(method_node, source)
                node_id = f"{rel_path}:{class_name}.{method_name}"
                nodes.append(BackendGraphNode(
                    id=node_id,
                    node_type=_classify_method(class_name, attrs),
                    file=rel_path,
                    name=f"{class_name}.{method_name}",
                    line=method_node.start_point[0] + 1,
                    attributes=attrs,
                ))

            for prop_node in _find_all(class_node, "property_declaration"):
                name_node = prop_node.child_by_field_name("name")
                if name_node is None:
                    continue
                prop_name = _text(name_node, source)
                node_id = f"{rel_path}:{class_name}.{prop_name}"
                nodes.append(BackendGraphNode(
                    id=node_id,
                    node_type=_classify_property(rel_path, class_name),
                    file=rel_path,
                    name=f"{class_name}.{prop_name}",
                    line=prop_node.start_point[0] + 1,
                ))

        return nodes

    def extract_file_edges(
        self,
        root_node: Any,
        source: bytes,
        rel_path: str,
        method_index: dict[str, str],
    ) -> list[BackendGraphEdge]:
        edges: list[BackendGraphEdge] = []

        for class_node in _find_all(root_node, "class_declaration"):
            class_name_node = class_node.child_by_field_name("name")
            if class_name_node is None:
                continue
            class_name = _text(class_name_node, source)

            field_types: dict[str, str] = {}
            for field_node in _find_all(class_node, "field_declaration"):
                field_src = source[field_node.start_byte:field_node.end_byte].decode("utf-8", errors="ignore")
                m = re.search(r"private\s+readonly\s+(\w+)\s+(_\w+)", field_src)
                if m:
                    field_types[m.group(2)] = m.group(1)

            for method_node in _find_all(class_node, "method_declaration"):
                name_node = method_node.child_by_field_name("name")
                if name_node is None:
                    continue
                method_name = _text(name_node, source)
                source_id = f"{rel_path}:{class_name}.{method_name}"
                attrs = _collect_attributes(method_node, source)
                method_src = source[method_node.start_byte:method_node.end_byte].decode("utf-8", errors="ignore")
                method_start_line = method_node.start_point[0] + 1

                if "Authorize" in attrs:
                    edges.append(BackendGraphEdge(
                        source=source_id,
                        target=f"auth:{class_name}.{method_name}",
                        edge_type="requires_auth",
                        evidence=f"{rel_path}:{method_start_line} [Authorize]",
                        confidence="high",
                    ))

                for attr in attrs:
                    if attr.startswith("Http"):
                        edges.append(BackendGraphEdge(
                            source=source_id,
                            target=f"endpoint:{class_name}.{method_name}",
                            edge_type="exposes_endpoint",
                            evidence=f"{rel_path}:{method_start_line} [{attr}]",
                            confidence="high",
                        ))
                        break

                for field_name, type_name in field_types.items():
                    pattern = rf"\b{re.escape(field_name)}\.(\w+)\s*\("
                    for call_m in re.finditer(pattern, method_src):
                        called = call_m.group(1)
                        target_id = method_index.get(f"{type_name}.{called}") or method_index.get(called)
                        if target_id and target_id != source_id:
                            line_offset = method_src[: call_m.start()].count("\n")
                            edges.append(BackendGraphEdge(
                                source=source_id,
                                target=target_id,
                                edge_type="calls",
                                evidence=f"{rel_path}:{method_start_line + line_offset} {call_m.group(0)}",
                                confidence="medium",
                            ))

                for db_m in re.finditer(r"SaveChanges(?:Async)?\s*\(", method_src):
                    line_offset = method_src[: db_m.start()].count("\n")
                    edges.append(BackendGraphEdge(
                        source=source_id,
                        target=f"db:{rel_path}:{method_start_line + line_offset}",
                        edge_type="writes_db",
                        evidence=f"{rel_path}:{method_start_line + line_offset} {db_m.group(0)}",
                        confidence="high",
                    ))

                for ext_m in re.finditer(r"(?:GetAsync|PostAsync|PutAsync|DeleteAsync|SendAsync)\s*\(", method_src):
                    line_offset = method_src[: ext_m.start()].count("\n")
                    edges.append(BackendGraphEdge(
                        source=source_id,
                        target=f"external:{rel_path}:{method_start_line + line_offset}",
                        edge_type="calls_external_api",
                        evidence=f"{rel_path}:{method_start_line + line_offset} {ext_m.group(0)}",
                        confidence="medium",
                    ))

        return edges


def _text(node: Any, source: bytes) -> str:
    if node is None:
        return ""
    return source[node.start_byte:node.end_byte].decode("utf-8", errors="ignore").strip()


def _first_line(node: Any, source: bytes) -> str:
    return _text(node, source).split("\n")[0].strip()


def _find_all(node: Any, node_type: str) -> list:
    results, stack = [], [node]
    while stack:
        n = stack.pop()
        if n.type == node_type:
            results.append(n)
        stack.extend(n.children)
    return results


def _collect_attributes(method_node: Any, source: bytes) -> list[str]:
    """attribute_list is a direct child of method_declaration."""
    attrs: list[str] = []
    for child in method_node.children:
        if child.type == "attribute_list":
            for grandchild in child.children:
                if grandchild.type == "attribute":
                    name_node = grandchild.child_by_field_name("name")
                    if name_node:
                        attrs.append(_text(name_node, source).split("(")[0])
    return attrs


def _enclosing_class_name(node: Any, source: bytes) -> str:
    current = node.parent
    while current is not None:
        if current.type == "class_declaration":
            name_node = current.child_by_field_name("name")
            if name_node:
                return _text(name_node, source)
        current = current.parent
    return ""


def _classify_method(class_name: str, attributes: list[str]) -> str:
    if class_name.endswith("Controller") or any(a.startswith("Http") for a in attributes):
        return "controller_action"
    if class_name.endswith("Service"):
        return "service_method"
    if class_name.endswith("Repository"):
        return "repository_method"
    return "method"


def _classify_property(rel_path: str, class_name: str) -> str:
    lower = f"{rel_path} {class_name}".lower()
    if "entity" in lower:
        return "entity_property"
    if any(t in lower for t in ("model", "dto", "request", "response")):
        return "model_property"
    return "property"


CSHARP_ADAPTER = CSharpAdapter()
