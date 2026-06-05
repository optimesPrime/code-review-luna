# phases/adapters/nodejs_adapter.py
from __future__ import annotations
import re
from typing import Any

from phases.backend_models import BackendChangedSymbol, BackendGraphEdge, BackendGraphNode

_HTTP_METHODS = {"get", "post", "put", "delete", "patch", "options", "head"}
_AUTH_DECORATORS = {"UseGuards", "AuthGuard", "JwtAuthGuard", "RolesGuard", "Roles"}
_NESTJS_ROUTE_DECORATORS = {"Get", "Post", "Put", "Delete", "Patch", "Options", "All"}


class NodejsAdapter:
    name = "nodejs"
    extensions = (".js", ".ts", ".mjs", ".cjs", ".tsx")

    def get_language(self) -> Any:
        import tree_sitter_typescript as tsts
        return tsts.language_typescript()

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
            if node.type in ("method_definition", "function_declaration", "arrow_function", "function"):
                break
            node = node.parent
        if node is None:
            return None

        func_name = _get_func_name(node, source)
        if not func_name:
            return None
        class_name = _enclosing_class_name(node, source)
        decorators = _collect_ts_decorators(node, source)
        sym_type = _classify_nodejs(func_name, class_name, decorators, rel_path)

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
        # NestJS class methods
        for class_node in _find_all(root_node, "class_declaration"):
            cn = class_node.child_by_field_name("name")
            if cn is None:
                continue
            class_name = _text(cn, source)
            class_decorators = _collect_class_decorators(class_node, source)
            class_body = next((c for c in class_node.children if c.type == "class_body"), None)
            if class_body is None:
                continue
            for method_node in _direct_methods(class_body):
                mn = method_node.child_by_field_name("name")
                if mn is None:
                    continue
                method_name = _text(mn, source)
                if method_name == "constructor":
                    continue
                decorators = _collect_ts_decorators(method_node, source)
                all_attrs = decorators + class_decorators
                node_id = f"{rel_path}:{class_name}.{method_name}"
                nodes.append(BackendGraphNode(
                    id=node_id,
                    node_type=_classify_nodejs(method_name, class_name, all_attrs, rel_path),
                    file=rel_path,
                    name=f"{class_name}.{method_name}",
                    line=method_node.start_point[0] + 1,
                    attributes=all_attrs,
                ))

        # Express route handlers: router.post('/path', ..., handler)
        for call_node in _find_all(root_node, "call_expression"):
            fn_node = call_node.child_by_field_name("function")
            if fn_node is None or fn_node.type != "member_expression":
                continue
            prop = fn_node.child_by_field_name("property")
            if prop is None or _text(prop, source).lower() not in _HTTP_METHODS:
                continue
            args_node = call_node.child_by_field_name("arguments")
            if args_node is None:
                continue
            # Get route path (first string arg)
            path_str = ""
            for arg in args_node.children:
                if arg.type == "string":
                    path_str = _text(arg, source).strip("'\"")
                    break
            method = _text(prop, source).lower()
            name = f"{method}:{path_str}" if path_str else method
            node_id = f"{rel_path}:{name}"
            nodes.append(BackendGraphNode(
                id=node_id, node_type="controller_action", file=rel_path,
                name=name, line=call_node.start_point[0] + 1,
                attributes=[method.upper()],
            ))
        return nodes

    def extract_file_edges(self, root_node, source, rel_path, method_index):
        edges = []
        for class_node in _find_all(root_node, "class_declaration"):
            cn = class_node.child_by_field_name("name")
            if cn is None:
                continue
            class_name = _text(cn, source)
            class_decorators = _collect_class_decorators(class_node, source)
            field_types = _collect_ts_field_types(class_node, source)
            class_body = next((c for c in class_node.children if c.type == "class_body"), None)
            if class_body is None:
                continue
            for method_node in _direct_methods(class_body):
                mn = method_node.child_by_field_name("name")
                if mn is None:
                    continue
                method_name = _text(mn, source)
                if method_name == "constructor":
                    continue
                source_id = f"{rel_path}:{class_name}.{method_name}"
                decorators = _collect_ts_decorators(method_node, source)
                all_attrs = decorators + class_decorators
                method_src = source[method_node.start_byte:method_node.end_byte].decode("utf-8", errors="ignore")
                start_line = method_node.start_point[0] + 1

                if any(a in _AUTH_DECORATORS for a in all_attrs):
                    edges.append(BackendGraphEdge(
                        source=source_id, target=f"auth:{class_name}.{method_name}",
                        edge_type="requires_auth",
                        evidence=f"{rel_path}:{start_line} @UseGuards", confidence="high",
                    ))

                for attr in all_attrs:
                    if attr in _NESTJS_ROUTE_DECORATORS:
                        edges.append(BackendGraphEdge(
                            source=source_id, target=f"endpoint:{class_name}.{method_name}",
                            edge_type="exposes_endpoint",
                            evidence=f"{rel_path}:{start_line} @{attr}", confidence="high",
                        ))
                        break

                for field_name, type_name in field_types.items():
                    pattern = rf"\bthis\.{re.escape(field_name)}\.(\w+)\s*\("
                    for m in re.finditer(pattern, method_src):
                        called = m.group(1)
                        target_id = method_index.get(f"{type_name}.{called}") or method_index.get(called)
                        if target_id and target_id != source_id:
                            offset = method_src[:m.start()].count("\n")
                            edges.append(BackendGraphEdge(
                                source=source_id, target=target_id, edge_type="calls",
                                evidence=f"{rel_path}:{start_line + offset} {m.group(0)}", confidence="medium",
                            ))

                for m in re.finditer(r"prisma\.\w+\.(create|update|delete|upsert)|\.save\s*\(|repository\.(save|delete)", method_src):
                    offset = method_src[:m.start()].count("\n")
                    edges.append(BackendGraphEdge(
                        source=source_id, target=f"db:{rel_path}:{start_line + offset}",
                        edge_type="writes_db",
                        evidence=f"{rel_path}:{start_line + offset} {m.group(0)}", confidence="high",
                    ))

                for m in re.finditer(r"axios\.|fetch\s*\(|http\.(get|post)", method_src):
                    offset = method_src[:m.start()].count("\n")
                    edges.append(BackendGraphEdge(
                        source=source_id, target=f"external:{rel_path}:{start_line + offset}",
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


def _direct_methods(class_body):
    return [c for c in class_body.children if c.type == "method_definition"]


def _get_func_name(node, source):
    if node.type == "method_definition":
        nn = node.child_by_field_name("name")
        return _text(nn, source) if nn else ""
    if node.type == "function_declaration":
        nn = node.child_by_field_name("name")
        return _text(nn, source) if nn else ""
    return ""


def _enclosing_class_name(node, source):
    cur = node.parent
    while cur is not None:
        if cur.type == "class_declaration":
            nn = cur.child_by_field_name("name")
            if nn:
                return _text(nn, source)
        cur = cur.parent
    return ""


def _extract_decorator_name(decorator_node, source):
    """从 decorator 节点提取装饰器名称。"""
    for part in decorator_node.children:
        if part.type == "identifier":
            return _text(part, source)
        elif part.type == "call_expression":
            fn = part.child_by_field_name("function")
            if fn:
                return _text(fn, source).split("(")[0]
    return ""


def _collect_preceding_decorators(target_node, parent, source):
    """收集紧邻 target_node 之前（连续）的所有 decorator 节点名称。"""
    siblings = list(parent.children)
    # tree-sitter 每次访问 .children 返回新对象，用位置范围匹配代替 is 比较
    target_start = target_node.start_byte
    target_end = target_node.end_byte
    try:
        idx = next(
            i for i, c in enumerate(siblings)
            if c.start_byte == target_start and c.end_byte == target_end
        )
    except StopIteration:
        return []
    # 倒序扫描，收集连续 decorator
    attrs = []
    for child in reversed(siblings[:idx]):
        if child.type == "decorator":
            name = _extract_decorator_name(child, source)
            if name:
                attrs.insert(0, name)
        else:
            break  # 遇到非 decorator 节点就停止
    return attrs


def _collect_ts_decorators(method_node, source):
    """TypeScript 装饰器是 class_body 中 method_definition 之前的兄弟 decorator 节点。"""
    parent = method_node.parent
    if parent is None:
        return []
    return _collect_preceding_decorators(method_node, parent, source)


def _collect_class_decorators(class_node, source):
    """类装饰器是 class_declaration（或其 export_statement 包装）之前的兄弟 decorator 节点。"""
    # 若 class 被 export_statement 包裹，装饰器是 export_statement 的兄弟
    target = class_node
    if class_node.parent and class_node.parent.type == "export_statement":
        target = class_node.parent

    parent = target.parent
    if parent is None:
        return []
    return _collect_preceding_decorators(target, parent, source)


def _collect_ts_field_types(class_node, source):
    """提取 NestJS 构造函数注入字段：private readonly orderService: OrderService"""
    fields = {}
    class_src = source[class_node.start_byte:class_node.end_byte].decode("utf-8", errors="ignore")
    for m in re.finditer(r"(?:private|protected)\s+(?:readonly\s+)?(\w+)\s*:\s*(\w+)", class_src):
        fields[m.group(1)] = m.group(2)
    return fields


def _classify_nodejs(func_name, class_name, decorators, rel_path):
    if any(d in _NESTJS_ROUTE_DECORATORS for d in decorators):
        return "controller_action"
    lower = f"{class_name} {rel_path}".lower()
    if "controller" in lower or "Controller" in class_name:
        return "controller_action"
    if any(d == "Injectable" for d in decorators) and "service" in lower:
        return "service_method"
    if "service" in lower:
        return "service_method"
    if "repository" in lower or "repo" in lower:
        return "repository_method"
    return "method"


def _module_name(rel_path):
    from pathlib import Path
    return Path(rel_path).stem


NODEJS_ADAPTER = NodejsAdapter()
