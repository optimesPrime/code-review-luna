# phases/adapters/python_adapter.py
from __future__ import annotations
import re
from typing import Any

from phases.backend_models import BackendChangedSymbol, BackendGraphEdge, BackendGraphNode


class PythonAdapter:
    name = "python"
    extensions = (".py",)

    def get_language(self) -> Any:
        import tree_sitter_python as tspy
        return tspy.language()

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

        fn = node.child_by_field_name("name")
        if fn is None:
            return None
        func_name = _text(fn, source)

        class_name = _enclosing_class_name(node, source)
        decorators = _collect_decorators(node, source)
        sym_type = _classify_python(func_name, class_name, decorators, rel_path)

        # 模块级非路由函数（没有类也没有路由装饰器）→ 不返回
        if not class_name and sym_type not in ("controller_action",):
            return None

        return BackendChangedSymbol(
            file=rel_path,
            symbol=func_name,
            symbol_type=sym_type,
            class_name=class_name or _module_name(rel_path),
            start_line=node.start_point[0] + 1,
            change_type="added" if is_new_file else "modified",
            attributes=decorators,
            evidence=f"{rel_path}:{node.start_point[0] + 1} {_first_line(node, source)}",
        )

    def extract_file_nodes(self, root_node, source, rel_path):
        nodes = []
        # 类方法
        for class_node in _find_all(root_node, "class_definition"):
            cn = class_node.child_by_field_name("name")
            if cn is None:
                continue
            class_name = _text(cn, source)
            for func_node in _direct_methods(class_node):
                fn = func_node.child_by_field_name("name")
                if fn is None:
                    continue
                func_name = _text(fn, source)
                if func_name.startswith("__") and func_name != "__init__":
                    continue
                decorators = _collect_decorators(func_node, source)
                sym_type = _classify_python(func_name, class_name, decorators, rel_path)
                node_id = f"{rel_path}:{class_name}.{func_name}"
                nodes.append(BackendGraphNode(
                    id=node_id, node_type=sym_type, file=rel_path,
                    name=f"{class_name}.{func_name}",
                    line=func_node.start_point[0] + 1, attributes=decorators,
                ))
        # 模块级装饰路由函数
        for deco_node in _find_all(root_node, "decorated_definition"):
            if deco_node.parent and deco_node.parent.type != "module":
                continue
            func_node = next((c for c in deco_node.children if c.type == "function_definition"), None)
            if func_node is None:
                continue
            fn = func_node.child_by_field_name("name")
            if fn is None:
                continue
            func_name = _text(fn, source)
            decorators = _collect_decorators(func_node, source)
            if not any(_is_route_decorator(d) for d in decorators):
                continue
            node_id = f"{rel_path}:{func_name}"
            nodes.append(BackendGraphNode(
                id=node_id, node_type="controller_action", file=rel_path,
                name=func_name, line=func_node.start_point[0] + 1, attributes=decorators,
            ))
        return nodes

    def extract_file_edges(self, root_node, source, rel_path, method_index):
        edges = []
        for node_id, func_node, func_name, class_name, decorators in _iter_all_functions(root_node, source, rel_path):
            method_src = source[func_node.start_byte:func_node.end_byte].decode("utf-8", errors="ignore")
            start_line = func_node.start_point[0] + 1

            # Auth: Depends 参数含 auth/user/current_user
            params_node = func_node.child_by_field_name("parameters")
            if params_node:
                params_text = source[params_node.start_byte:params_node.end_byte].decode("utf-8", errors="ignore")
                if re.search(r"Depends\s*\(|Security\s*\(", params_text):
                    if re.search(r"current_user|auth|user|token|principal", params_text, re.IGNORECASE):
                        edges.append(BackendGraphEdge(
                            source=node_id, target=f"auth:{node_id}",
                            edge_type="requires_auth",
                            evidence=f"{rel_path}:{start_line} Depends(auth)", confidence="high",
                        ))

            # DB writes
            for m in re.finditer(r"\.commit\s*\(|\.save\s*\(|\.delete\s*\(|\.add\s*\(|session\.add|db\.add", method_src):
                offset = method_src[:m.start()].count("\n")
                edges.append(BackendGraphEdge(
                    source=node_id, target=f"db:{rel_path}:{start_line + offset}",
                    edge_type="writes_db",
                    evidence=f"{rel_path}:{start_line + offset} {m.group(0)}", confidence="high",
                ))

            # External
            for m in re.finditer(r"requests\.(get|post|put|delete)|httpx\.", method_src):
                offset = method_src[:m.start()].count("\n")
                edges.append(BackendGraphEdge(
                    source=node_id, target=f"external:{rel_path}:{start_line + offset}",
                    edge_type="calls_external_api",
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


def _direct_methods(class_node):
    block = next((c for c in class_node.children if c.type == "block"), None)
    if block is None:
        return []
    result = []
    for child in block.children:
        if child.type == "function_definition":
            result.append(child)
        elif child.type == "decorated_definition":
            fn = next((c for c in child.children if c.type == "function_definition"), None)
            if fn:
                result.append(fn)
    return result


def _collect_decorators(func_node, source):
    attrs = []
    parent = func_node.parent
    if parent and parent.type == "decorated_definition":
        for child in parent.children:
            if child.type == "decorator":
                for part in child.children:
                    if part.type in ("identifier", "attribute"):
                        attrs.append(_text(part, source).split("(")[0])
                        break
                    elif part.type == "call":
                        fn = part.child_by_field_name("function")
                        if fn:
                            attrs.append(_text(fn, source).split("(")[0])
                        break
    return attrs


def _is_route_decorator(deco_text):
    lower = deco_text.lower()
    return any(m in lower for m in (".get", ".post", ".put", ".delete", ".patch", ".options", "route", "api_view"))


def _enclosing_class_name(func_node, source):
    cur = func_node.parent
    while cur is not None:
        if cur.type == "class_definition":
            nn = cur.child_by_field_name("name")
            if nn:
                return _text(nn, source)
        cur = cur.parent
    return ""


def _module_name(rel_path):
    from pathlib import Path
    return Path(rel_path).stem


def _classify_python(func_name, class_name, decorators, rel_path):
    if any(_is_route_decorator(d) for d in decorators):
        return "controller_action"
    lower = f"{class_name} {rel_path}".lower()
    if "service" in lower:
        return "service_method"
    if "repo" in lower or "repository" in lower:
        return "repository_method"
    return "method"


def _iter_all_functions(root_node, source, rel_path):
    for class_node in _find_all(root_node, "class_definition"):
        cn = class_node.child_by_field_name("name")
        if cn is None:
            continue
        class_name = _text(cn, source)
        for func_node in _direct_methods(class_node):
            fn = func_node.child_by_field_name("name")
            if fn is None:
                continue
            func_name = _text(fn, source)
            decorators = _collect_decorators(func_node, source)
            yield f"{rel_path}:{class_name}.{func_name}", func_node, func_name, class_name, decorators
    for deco_node in _find_all(root_node, "decorated_definition"):
        if deco_node.parent and deco_node.parent.type != "module":
            continue
        func_node = next((c for c in deco_node.children if c.type == "function_definition"), None)
        if func_node is None:
            continue
        fn = func_node.child_by_field_name("name")
        if fn is None:
            continue
        func_name = _text(fn, source)
        decorators = _collect_decorators(func_node, source)
        yield f"{rel_path}:{func_name}", func_node, func_name, "", decorators


PYTHON_ADAPTER = PythonAdapter()
