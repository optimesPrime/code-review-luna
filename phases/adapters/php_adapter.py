# phases/adapters/php_adapter.py
from __future__ import annotations
import re
from typing import Any

from phases.backend_models import BackendChangedSymbol, BackendGraphEdge, BackendGraphNode


class PhpAdapter:
    name = "php"
    extensions = (".php",)

    def get_language(self) -> Any:
        import tree_sitter_php as tsphp
        return tsphp.language_php()

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
        if method_name == "__construct":
            return None

        return BackendChangedSymbol(
            file=rel_path,
            symbol=method_name,
            symbol_type=_classify_php(method_name, class_name, rel_path),
            class_name=class_name,
            start_line=node.start_point[0] + 1,
            change_type="added" if is_new_file else "modified",
            attributes=[],
            evidence=f"{rel_path}:{node.start_point[0] + 1} {_first_line(node, source)}",
        )

    def extract_file_nodes(self, root_node, source, rel_path):
        nodes = []
        for class_node in _find_all(root_node, "class_declaration"):
            cn = class_node.child_by_field_name("name")
            if cn is None:
                continue
            class_name = _text(cn, source)
            for method_node in _find_all(class_node, "method_declaration"):
                mn = method_node.child_by_field_name("name")
                if mn is None:
                    continue
                method_name = _text(mn, source)
                if method_name == "__construct":
                    continue
                node_id = f"{rel_path}:{class_name}.{method_name}"
                nodes.append(BackendGraphNode(
                    id=node_id, node_type=_classify_php(method_name, class_name, rel_path),
                    file=rel_path, name=f"{class_name}.{method_name}",
                    line=method_node.start_point[0] + 1,
                ))
        return nodes

    def extract_file_edges(self, root_node, source, rel_path, method_index):
        edges = []
        for class_node in _find_all(root_node, "class_declaration"):
            cn = class_node.child_by_field_name("name")
            if cn is None:
                continue
            class_name = _text(cn, source)
            field_types = _collect_php_injections(class_node, source)

            for method_node in _find_all(class_node, "method_declaration"):
                mn = method_node.child_by_field_name("name")
                if mn is None:
                    continue
                method_name = _text(mn, source)
                if method_name == "__construct":
                    continue
                source_id = f"{rel_path}:{class_name}.{method_name}"
                method_src = source[method_node.start_byte:method_node.end_byte].decode("utf-8", errors="ignore")
                start_line = method_node.start_point[0] + 1

                if re.search(r"\$this->authorize\s*\(|auth\(\)|->middleware\s*\(\s*['\"]auth", method_src):
                    edges.append(BackendGraphEdge(
                        source=source_id, target=f"auth:{class_name}.{method_name}",
                        edge_type="requires_auth",
                        evidence=f"{rel_path}:{start_line} authorize()", confidence="high",
                    ))

                for field_name, type_name in field_types.items():
                    pattern = rf"\$this->{re.escape(field_name)}->(\w+)\s*\("
                    for m in re.finditer(pattern, method_src):
                        called = m.group(1)
                        target_id = method_index.get(f"{type_name}.{called}") or method_index.get(called)
                        if target_id and target_id != source_id:
                            offset = method_src[:m.start()].count("\n")
                            edges.append(BackendGraphEdge(
                                source=source_id, target=target_id, edge_type="calls",
                                evidence=f"{rel_path}:{start_line + offset} {m.group(0)}", confidence="medium",
                            ))

                for m in re.finditer(r"->save\s*\(|->delete\s*\(|->create\s*\(|DB::transaction|->update\s*\(", method_src):
                    offset = method_src[:m.start()].count("\n")
                    edges.append(BackendGraphEdge(
                        source=source_id, target=f"db:{rel_path}:{start_line + offset}",
                        edge_type="writes_db",
                        evidence=f"{rel_path}:{start_line + offset} {m.group(0)}", confidence="high",
                    ))
        return edges


def _text(node, source):
    if node is None:
        return ""
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


def _enclosing_class_name(node, source):
    cur = node.parent
    while cur is not None:
        if cur.type == "class_declaration":
            nn = cur.child_by_field_name("name")
            if nn:
                return _text(nn, source)
        cur = cur.parent
    return ""


def _classify_php(method_name, class_name, rel_path):
    lower = f"{class_name} {rel_path}".lower()
    if "controller" in lower:
        return "controller_action"
    if "service" in lower:
        return "service_method"
    if "repository" in lower or "repo" in lower:
        return "repository_method"
    return "method"


def _collect_php_injections(class_node, source):
    fields = {}
    for method in _find_all(class_node, "method_declaration"):
        mn = method.child_by_field_name("name")
        if mn is None or _text(mn, source) != "__construct":
            continue
        method_src = source[method.start_byte:method.end_byte].decode("utf-8", errors="ignore")
        params = {}
        for m in re.finditer(r"(\w+)\s+\$(\w+)", method_src):
            params[m.group(2)] = m.group(1)
        for m in re.finditer(r"\$this->(\w+)\s*=\s*\$(\w+)", method_src):
            field_name, param_name = m.group(1), m.group(2)
            if param_name in params:
                fields[field_name] = params[param_name]
    return fields


PHP_ADAPTER = PhpAdapter()
