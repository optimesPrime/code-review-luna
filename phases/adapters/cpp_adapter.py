# phases/adapters/cpp_adapter.py
from __future__ import annotations
import re
from typing import Any

from phases.backend_models import BackendChangedSymbol, BackendGraphEdge, BackendGraphNode


class CppAdapter:
    name = "cpp"
    extensions = (".cpp", ".cc", ".cxx", ".h", ".hpp")

    def get_language(self) -> Any:
        import tree_sitter_cpp as tscpp
        return tscpp.language()

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
            if node.type == "function_definition":
                break
            node = node.parent
        if node is None:
            return None

        func_name, class_name = _get_cpp_func_info(node, source)
        if not func_name:
            return None

        # Infer class_name from enclosing class_specifier if not already known
        if not class_name:
            class_name = _enclosing_class_name(node, source)

        sym_type = _classify_cpp(func_name, class_name, source, node)

        return BackendChangedSymbol(
            file=rel_path,
            symbol=func_name,
            symbol_type=sym_type,
            class_name=class_name or "",
            start_line=node.start_point[0] + 1,
            change_type="added" if is_new_file else "modified",
            attributes=[],
            evidence=f"{rel_path}:{node.start_point[0] + 1} {_first_line(node, source)}",
        )

    def extract_file_nodes(self, root_node, source, rel_path):
        nodes = []
        # Class methods
        for class_node in _find_all(root_node, "class_specifier"):
            cn = class_node.child_by_field_name("name")
            if cn is None:
                continue
            class_name = _text(cn, source)
            fdl = next((c for c in class_node.children if c.type == "field_declaration_list"), None)
            if fdl is None:
                continue
            for func_node in _find_all(fdl, "function_definition"):
                func_name, _ = _get_cpp_func_info(func_node, source)
                if not func_name:
                    continue
                sym_type = _classify_cpp(func_name, class_name, source, func_node)
                node_id = f"{rel_path}:{class_name}.{func_name}"
                nodes.append(BackendGraphNode(
                    id=node_id, node_type=sym_type, file=rel_path,
                    name=f"{class_name}.{func_name}", line=func_node.start_point[0] + 1,
                ))
        # Top-level functions
        for child in root_node.children:
            if child.type == "function_definition":
                func_name, _ = _get_cpp_func_info(child, source)
                if not func_name:
                    continue
                sym_type = _classify_cpp(func_name, "", source, child)
                node_id = f"{rel_path}:{func_name}"
                nodes.append(BackendGraphNode(
                    id=node_id, node_type=sym_type, file=rel_path,
                    name=func_name, line=child.start_point[0] + 1,
                ))
        return nodes

    def extract_file_edges(self, root_node, source, rel_path, method_index):
        edges = []
        all_funcs = []
        for class_node in _find_all(root_node, "class_specifier"):
            cn = class_node.child_by_field_name("name")
            class_name = _text(cn, source) if cn else ""
            fdl = next((c for c in class_node.children if c.type == "field_declaration_list"), None)
            if fdl:
                for func_node in _find_all(fdl, "function_definition"):
                    func_name, _ = _get_cpp_func_info(func_node, source)
                    if func_name:
                        all_funcs.append((f"{rel_path}:{class_name}.{func_name}", func_node))
        for child in root_node.children:
            if child.type == "function_definition":
                func_name, _ = _get_cpp_func_info(child, source)
                if func_name:
                    all_funcs.append((f"{rel_path}:{func_name}", child))

        for source_id, func_node in all_funcs:
            method_src = source[func_node.start_byte:func_node.end_byte].decode("utf-8", errors="ignore")
            start_line = func_node.start_point[0] + 1

            for m in re.finditer(r"lock_guard|unique_lock|\.lock\s*\(|\.unlock\s*\(|std::thread|std::async", method_src):
                offset = method_src[:m.start()].count("\n")
                edges.append(BackendGraphEdge(
                    source=source_id, target=f"concurrency:{rel_path}:{start_line + offset}",
                    edge_type="concurrency_boundary",
                    evidence=f"{rel_path}:{start_line + offset} {m.group(0)}", confidence="high",
                ))

            for m in re.finditer(r"\bdelete\s+|std::move\s*\(|\.reset\s*\(|std::make_unique", method_src):
                offset = method_src[:m.start()].count("\n")
                edges.append(BackendGraphEdge(
                    source=source_id, target=f"memory:{rel_path}:{start_line + offset}",
                    edge_type="memory_ownership",
                    evidence=f"{rel_path}:{start_line + offset} {m.group(0)}", confidence="medium",
                ))

            for m in re.finditer(r"\.Save\s*\(|\.Insert\s*\(|\.Update\s*\(|\.Delete\s*\(|\.Write\s*\(", method_src):
                offset = method_src[:m.start()].count("\n")
                edges.append(BackendGraphEdge(
                    source=source_id, target=f"storage:{rel_path}:{start_line + offset}",
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


def _get_cpp_func_info(func_node, source):
    declarator = func_node.child_by_field_name("declarator")
    if declarator is None:
        return ("", "")
    if declarator.type == "function_declarator":
        inner = declarator.child_by_field_name("declarator")
        if inner is None:
            return ("", "")
        if inner.type == "field_identifier":
            return (_text(inner, source), "")
        if inner.type == "identifier":
            return (_text(inner, source), "")
        if inner.type == "qualified_identifier":
            scope = inner.child_by_field_name("scope")
            name = inner.child_by_field_name("name")
            if name:
                return (_text(name, source), _text(scope, source) if scope else "")
    return ("", "")


def _enclosing_class_name(node, source):
    cur = node.parent
    while cur is not None:
        if cur.type == "class_specifier":
            nn = cur.child_by_field_name("name")
            if nn:
                return _text(nn, source)
        cur = cur.parent
    return ""


def _classify_cpp(func_name, class_name, source, node):
    decl = node.child_by_field_name("declarator")
    params_src = ""
    if decl:
        pl = next((c for c in decl.children if c.type == "parameter_list"), None)
        if pl:
            params_src = source[pl.start_byte:pl.end_byte].decode("utf-8", errors="ignore")
    if "ServerContext" in params_src or "HttpRequest" in params_src:
        return "controller_action"
    lower = f"{class_name} {func_name}".lower()
    if "service" in lower:
        return "service_method"
    if "repo" in lower or "repository" in lower or "store" in lower:
        return "repository_method"
    if "handler" in lower:
        return "controller_action"
    return "method"


CPP_ADAPTER = CppAdapter()
