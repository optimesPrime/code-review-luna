# phases/adapters/go_adapter.py
from __future__ import annotations
import re
from typing import Any

from phases.backend_models import BackendChangedSymbol, BackendGraphEdge, BackendGraphNode


class GoAdapter:
    name = "go"
    extensions = (".go",)

    def get_language(self) -> Any:
        import tree_sitter_go as tsgo
        return tsgo.language()

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
            if node.type in ("function_declaration", "method_declaration"):
                break
            node = node.parent
        if node is None:
            return None

        func_name, receiver_type = _get_go_func_info(node, source)
        if not func_name:
            return None
        sym_type = _classify_go(func_name, receiver_type, source, node, rel_path)
        class_name = receiver_type or _package_name(root_node, source)

        return BackendChangedSymbol(
            file=rel_path,
            symbol=func_name,
            symbol_type=sym_type,
            class_name=class_name,
            start_line=node.start_point[0] + 1,
            change_type="added" if is_new_file else "modified",
            attributes=[],
            evidence=f"{rel_path}:{node.start_point[0] + 1} {_first_line(node, source)}",
        )

    def extract_file_nodes(self, root_node, source, rel_path):
        nodes = []
        for func_node in _find_all(root_node, "function_declaration"):
            fn = func_node.child_by_field_name("name")
            if fn is None:
                continue
            func_name = _text(fn, source)
            sym_type = _classify_go(func_name, "", source, func_node, rel_path)
            nodes.append(BackendGraphNode(
                id=f"{rel_path}:{func_name}", node_type=sym_type, file=rel_path,
                name=func_name, line=func_node.start_point[0] + 1,
            ))
        for method_node in _find_all(root_node, "method_declaration"):
            func_name, receiver_type = _get_go_func_info(method_node, source)
            if not func_name:
                continue
            sym_type = _classify_go(func_name, receiver_type, source, method_node, rel_path)
            name = f"{receiver_type}.{func_name}" if receiver_type else func_name
            nodes.append(BackendGraphNode(
                id=f"{rel_path}:{name}", node_type=sym_type, file=rel_path,
                name=name, line=method_node.start_point[0] + 1,
            ))
        return nodes

    def extract_file_edges(self, root_node, source, rel_path, method_index):
        edges = []
        struct_fields = _collect_struct_fields(root_node, source)

        all_funcs = []
        for func_node in _find_all(root_node, "function_declaration"):
            fn = func_node.child_by_field_name("name")
            if fn:
                all_funcs.append((f"{rel_path}:{_text(fn, source)}", func_node, ""))
        for method_node in _find_all(root_node, "method_declaration"):
            func_name, receiver_type = _get_go_func_info(method_node, source)
            if func_name:
                name = f"{receiver_type}.{func_name}" if receiver_type else func_name
                all_funcs.append((f"{rel_path}:{name}", method_node, receiver_type))

        for source_id, func_node, receiver_type in all_funcs:
            method_src = source[func_node.start_byte:func_node.end_byte].decode("utf-8", errors="ignore")
            start_line = func_node.start_point[0] + 1

            fields = struct_fields.get(receiver_type, {})
            for field_name, type_name in fields.items():
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

            for m in re.finditer(r"\.Create\s*\(|\.Save\s*\(|\.Delete\s*\(|\.Updates\s*\(|tx\.Commit\s*\(", method_src):
                offset = method_src[:m.start()].count("\n")
                edges.append(BackendGraphEdge(
                    source=source_id, target=f"db:{rel_path}:{start_line + offset}",
                    edge_type="writes_db",
                    evidence=f"{rel_path}:{start_line + offset} {m.group(0)}", confidence="high",
                ))

            for m in re.finditer(r"\.Lock\s*\(|\.Unlock\s*\(|sync\.Mutex|\bgo\s+\w+\(", method_src):
                offset = method_src[:m.start()].count("\n")
                edges.append(BackendGraphEdge(
                    source=source_id, target=f"concurrency:{rel_path}:{start_line + offset}",
                    edge_type="concurrency_boundary",
                    evidence=f"{rel_path}:{start_line + offset} {m.group(0)}", confidence="medium",
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


def _get_go_func_info(node, source):
    if node.type == "function_declaration":
        fn = node.child_by_field_name("name")
        return (_text(fn, source) if fn else "", "")
    if node.type == "method_declaration":
        fn = next((c for c in node.children if c.type == "field_identifier"), None)
        recv_list = next((c for c in node.children if c.type == "parameter_list"), None)
        receiver_type = ""
        if recv_list:
            for param in recv_list.children:
                if param.type == "parameter_declaration":
                    for tc in param.children:
                        if tc.type == "pointer_type":
                            ti = next((c for c in tc.children if c.type == "type_identifier"), None)
                            if ti:
                                receiver_type = _text(ti, source)
                        elif tc.type == "type_identifier":
                            receiver_type = _text(tc, source)
        return (_text(fn, source) if fn else "", receiver_type)
    return ("", "")


def _classify_go(func_name, struct_name, source, node, rel_path):
    params = ""
    for child in node.children:
        if child.type == "parameter_list":
            params = source[child.start_byte:child.end_byte].decode("utf-8", errors="ignore")
            break
    if "gin.Context" in params or "echo.Context" in params or "fiber.Ctx" in params:
        return "controller_action"
    if "http.ResponseWriter" in params:
        return "controller_action"
    lower = f"{struct_name} {rel_path}".lower()
    if "service" in lower:
        return "service_method"
    if "repo" in lower or "repository" in lower or "store" in lower:
        return "repository_method"
    if "handler" in lower:
        return "controller_action"
    return "method"


def _collect_struct_fields(root_node, source):
    structs = {}
    for type_decl in _find_all(root_node, "type_declaration"):
        for type_spec in type_decl.children:
            if type_spec.type != "type_spec":
                continue
            name_node = type_spec.child_by_field_name("name")
            if name_node is None:
                continue
            struct_name = _text(name_node, source)
            struct_type = next((c for c in type_spec.children if c.type == "struct_type"), None)
            if struct_type is None:
                continue
            fields = {}
            for fd in _find_all(struct_type, "field_declaration"):
                fd_src = source[fd.start_byte:fd.end_byte].decode("utf-8", errors="ignore")
                m = re.search(r"(\w+)\s+\*?(\w+)", fd_src)
                if m:
                    fields[m.group(1)] = m.group(2)
            structs[struct_name] = fields
    return structs


def _package_name(root_node, source):
    for child in root_node.children:
        if child.type == "package_clause":
            pid = next((c for c in child.children if c.type == "package_identifier"), None)
            if pid:
                return _text(pid, source)
    return ""


GOLANG_ADAPTER = GoAdapter()
