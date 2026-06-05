# phases/adapters/java_adapter.py
from __future__ import annotations
import re
from typing import Any

from phases.backend_models import BackendChangedSymbol, BackendGraphEdge, BackendGraphNode


class JavaAdapter:
    name = "java"
    extensions = (".java",)

    def get_language(self) -> Any:
        import tree_sitter_java as tsjava
        return tsjava.language()

    def find_enclosing_symbol(self, root_node, source, line, rel_path, is_new_file):
        target_line = line - 1
        lines = source.decode("utf-8", errors="ignore").split("\n")
        raw = lines[target_line] if target_line < len(lines) else ""
        col = len(raw) - len(raw.lstrip()) if raw.strip() else 0
        leaf = root_node.descendant_for_point_range((target_line, col), (target_line, col))
        if leaf is None:
            return None

        node = leaf
        while node is not None:
            if node.type == "method_declaration":
                break
            node = node.parent
        if node is None:
            return None

        class_name = _enclosing_class_name(node, source)
        if not class_name:
            return None
        name_node = node.child_by_field_name("name")
        if name_node is None:
            return None
        method_name = _text(name_node, source)
        method_attrs = _collect_annotations(node, source)
        class_node = _find_class_node(node)
        class_attrs = _collect_annotations(class_node, source) if class_node else []

        return BackendChangedSymbol(
            file=rel_path,
            symbol=method_name,
            symbol_type=_classify_method(class_name, method_attrs, class_attrs),
            class_name=class_name,
            start_line=node.start_point[0] + 1,
            change_type="added" if is_new_file else "modified",
            attributes=method_attrs + class_attrs,
            evidence=f"{rel_path}:{node.start_point[0] + 1} {_first_line(node, source)}",
        )

    def extract_file_nodes(self, root_node, source, rel_path):
        nodes = []
        for class_node in _find_all(root_node, "class_declaration"):
            cn = class_node.child_by_field_name("name")
            if cn is None:
                continue
            class_name = _text(cn, source)
            class_attrs = _collect_annotations(class_node, source)
            for method_node in _find_all(class_node, "method_declaration"):
                mn = method_node.child_by_field_name("name")
                if mn is None:
                    continue
                method_name = _text(mn, source)
                method_attrs = _collect_annotations(method_node, source)
                all_attrs = method_attrs + class_attrs
                node_id = f"{rel_path}:{class_name}.{method_name}"
                nodes.append(BackendGraphNode(
                    id=node_id,
                    node_type=_classify_method(class_name, method_attrs, class_attrs),
                    file=rel_path,
                    name=f"{class_name}.{method_name}",
                    line=method_node.start_point[0] + 1,
                    attributes=all_attrs,
                ))
        return nodes

    def extract_file_edges(self, root_node, source, rel_path, method_index):
        edges = []
        for class_node in _find_all(root_node, "class_declaration"):
            cn = class_node.child_by_field_name("name")
            if cn is None:
                continue
            class_name = _text(cn, source)
            class_attrs = _collect_annotations(class_node, source)
            field_types = _collect_field_types(class_node, source)

            for method_node in _find_all(class_node, "method_declaration"):
                mn = method_node.child_by_field_name("name")
                if mn is None:
                    continue
                method_name = _text(mn, source)
                source_id = f"{rel_path}:{class_name}.{method_name}"
                method_attrs = _collect_annotations(method_node, source)
                all_attrs = method_attrs + class_attrs
                method_src = source[method_node.start_byte:method_node.end_byte].decode("utf-8", errors="ignore")
                start_line = method_node.start_point[0] + 1

                if any(a in ("PreAuthorize", "Secured", "RolesAllowed") for a in all_attrs):
                    edges.append(BackendGraphEdge(
                        source=source_id, target=f"auth:{class_name}.{method_name}",
                        edge_type="requires_auth",
                        evidence=f"{rel_path}:{start_line} @PreAuthorize", confidence="high",
                    ))

                for attr in all_attrs:
                    if any(attr.startswith(p) for p in ("GetMapping", "PostMapping", "PutMapping", "DeleteMapping", "PatchMapping", "RequestMapping")):
                        edges.append(BackendGraphEdge(
                            source=source_id, target=f"endpoint:{class_name}.{method_name}",
                            edge_type="exposes_endpoint",
                            evidence=f"{rel_path}:{start_line} @{attr}", confidence="high",
                        ))
                        break

                for field_name, type_name in field_types.items():
                    pattern = rf"\b{re.escape(field_name)}\.(\w+)\s*\("
                    for m in re.finditer(pattern, method_src):
                        called = m.group(1)
                        target_id = method_index.get(f"{type_name}.{called}") or method_index.get(called)
                        if target_id and target_id != source_id:
                            offset = method_src[:m.start()].count("\n")
                            edges.append(BackendGraphEdge(
                                source=source_id, target=target_id, edge_type="calls",
                                evidence=f"{rel_path}:{start_line + offset} {m.group(0)}", confidence="medium",
                            ))

                for m in re.finditer(r"\.save\(|\.delete\(|\.saveAll\(|\.saveAndFlush\(", method_src):
                    offset = method_src[:m.start()].count("\n")
                    edges.append(BackendGraphEdge(
                        source=source_id, target=f"db:{rel_path}:{start_line + offset}",
                        edge_type="writes_db",
                        evidence=f"{rel_path}:{start_line + offset} {m.group(0)}", confidence="high",
                    ))

                for m in re.finditer(r"restTemplate\.|webClient\.|httpClient\.", method_src):
                    offset = method_src[:m.start()].count("\n")
                    edges.append(BackendGraphEdge(
                        source=source_id, target=f"external:{rel_path}:{start_line + offset}",
                        edge_type="calls_external_api",
                        evidence=f"{rel_path}:{start_line + offset} {m.group(0)}", confidence="medium",
                    ))
        return edges


def _text(node, source):
    return source[node.start_byte:node.end_byte].decode("utf-8", errors="ignore").strip()


def _first_line(node, source):
    return _text(node, source).split("\n")[0].strip()


def _find_all(node, node_type):
    results, stack = [], [node]
    while stack:
        n = stack.pop()
        if n.type == node_type:
            results.append(n)
        stack.extend(n.children)
    return results


def _collect_annotations(node, source):
    attrs = []
    for child in node.children:
        if child.type == "modifiers":
            for mod in child.children:
                if mod.type in ("marker_annotation", "annotation"):
                    name_node = mod.child_by_field_name("name")
                    if name_node:
                        attrs.append(_text(name_node, source).split("(")[0])
    return attrs


def _collect_field_types(class_node, source):
    fields = {}
    for fd in _find_all(class_node, "field_declaration"):
        field_src = source[fd.start_byte:fd.end_byte].decode("utf-8", errors="ignore")
        m = re.search(r"(?:private|protected)\s+(?:final\s+)?(\w+)\s+(\w+)\s*[;=]", field_src)
        if m:
            fields[m.group(2)] = m.group(1)
    return fields


def _enclosing_class_name(node, source):
    cur = node.parent
    while cur is not None:
        if cur.type == "class_declaration":
            nn = cur.child_by_field_name("name")
            if nn:
                return _text(nn, source)
        cur = cur.parent
    return ""


def _find_class_node(node):
    cur = node.parent
    while cur is not None:
        if cur.type == "class_declaration":
            return cur
        cur = cur.parent
    return None


def _classify_method(class_name, method_attrs, class_attrs):
    all_attrs = method_attrs + class_attrs
    if any(a in ("RestController", "Controller") for a in class_attrs):
        return "controller_action"
    if any(a.endswith("Mapping") for a in all_attrs):
        return "controller_action"
    if any(a == "Service" for a in class_attrs) or class_name.endswith("Service"):
        return "service_method"
    if any(a in ("Repository",) for a in class_attrs) or class_name.endswith("Repository"):
        return "repository_method"
    return "method"


JAVA_ADAPTER = JavaAdapter()
