from __future__ import annotations

import json
from dataclasses import dataclass, field


@dataclass
class APIChangeItem:
    file: str
    line: int
    change_type: str    # e.g. "removed_endpoint", "changed_field_type", ...
    path: str           # human-readable path description
    risk: str           # "high" | "medium" | "low"
    reason: str
    suggestion: str
    needs_human_review: bool = True


def _load(content: str) -> dict:
    """Parse YAML or JSON content into a dict."""
    content = content.strip()
    if content.startswith("{"):
        return json.loads(content)
    try:
        import yaml
        return yaml.safe_load(content) or {}
    except Exception:
        return {}


def _get_paths(spec: dict) -> dict:
    return spec.get("paths") or {}


def _get_schema(method_obj: dict) -> dict:
    """Extract request body schema from a method object."""
    rb = method_obj.get("requestBody") or {}
    content = rb.get("content") or {}
    for media in content.values():
        schema = (media or {}).get("schema") or {}
        if schema:
            return schema
    return {}


def analyze(old_content: str, new_content: str, file_path: str) -> list[APIChangeItem]:
    """Compare two OpenAPI spec versions and return a list of breaking changes."""
    old = _load(old_content)
    new = _load(new_content)
    if not old or not new:
        return []

    items: list[APIChangeItem] = []
    old_paths = _get_paths(old)
    new_paths = _get_paths(new)

    # ── Removed endpoints / methods ──────────────────────────────────────────
    for path, old_methods in old_paths.items():
        if path not in new_paths:
            items.append(APIChangeItem(
                file=file_path, line=0,
                change_type="removed_endpoint",
                path=path,
                risk="high",
                reason=f"endpoint {path} 被删除，调用方将收到 404",
                suggestion="先标记为 deprecated，保留至少一个版本周期后再删除",
            ))
            continue
        new_methods = new_paths[path]
        for method, old_obj in (old_methods or {}).items():
            if method not in (new_methods or {}):
                items.append(APIChangeItem(
                    file=file_path, line=0,
                    change_type="removed_method",
                    path=f"{method.upper()} {path}",
                    risk="high",
                    reason=f"{method.upper()} {path} 方法被删除",
                    suggestion="先标记 deprecated，给调用方迁移时间",
                ))
                continue
            new_obj = new_methods[method]
            items.extend(_diff_method(old_obj or {}, new_obj or {}, method, path, file_path))

    # ── New endpoints (low risk) ──────────────────────────────────────────────
    for path in new_paths:
        if path not in old_paths:
            new_methods = new_paths[path] or {}
            for method in new_methods:
                items.append(APIChangeItem(
                    file=file_path, line=0,
                    change_type="added_endpoint",
                    path=f"{method.upper()} {path}",
                    risk="low",
                    reason=f"新增接口 {method.upper()} {path}，向后兼容",
                    suggestion="",
                ))

    return items


def _diff_method(
    old_obj: dict,
    new_obj: dict,
    method: str,
    path: str,
    file_path: str,
) -> list[APIChangeItem]:
    items: list[APIChangeItem] = []
    label = f"{method.upper()} {path}"

    old_schema = _get_schema(old_obj)
    new_schema = _get_schema(new_obj)

    if old_schema or new_schema:
        items.extend(_diff_schema(old_schema, new_schema, label, file_path))

    return items


def _diff_schema(
    old_schema: dict,
    new_schema: dict,
    context: str,
    file_path: str,
) -> list[APIChangeItem]:
    items: list[APIChangeItem] = []

    old_props = old_schema.get("properties") or {}
    new_props = new_schema.get("properties") or {}
    old_required = set(old_schema.get("required") or [])
    new_required = set(new_schema.get("required") or [])

    # Removed required fields
    for field_name in old_required:
        if field_name not in new_props:
            items.append(APIChangeItem(
                file=file_path, line=0,
                change_type="removed_required_field",
                path=f"{context} → {field_name}",
                risk="high",
                reason=f"必填字段 {field_name} 被删除，旧客户端发送的请求可能报错",
                suggestion="先改为 optional，确认无客户端依赖后再删除",
            ))

    # Changed field types
    for field_name, old_prop in old_props.items():
        if field_name in new_props:
            old_type = (old_prop or {}).get("type")
            new_type = (new_props[field_name] or {}).get("type")
            if old_type and new_type and old_type != new_type:
                items.append(APIChangeItem(
                    file=file_path, line=0,
                    change_type="changed_field_type",
                    path=f"{context} → {field_name}",
                    risk="high",
                    reason=f"字段 {field_name} 类型 {old_type} → {new_type}，序列化不兼容",
                    suggestion="保持原类型，用新字段名提供新类型，版本稳定后再废弃旧字段",
                ))

    # Newly required fields (not in old required)
    for field_name in new_required - old_required:
        if field_name not in old_props:
            # Brand new required field
            items.append(APIChangeItem(
                file=file_path, line=0,
                change_type="added_required_field",
                path=f"{context} → {field_name}",
                risk="high",
                reason=f"新增必填字段 {field_name}，旧客户端请求缺少该字段将被拒绝",
                suggestion="先设为 optional 并赋默认值，待所有客户端升级后再改为必填",
            ))
        elif field_name in old_props:
            # Existing optional field promoted to required
            items.append(APIChangeItem(
                file=file_path, line=0,
                change_type="added_required_field",
                path=f"{context} → {field_name}",
                risk="high",
                reason=f"字段 {field_name} 从 optional 改为 required，旧客户端请求将被拒绝",
                suggestion="先确保所有调用方都已传递该字段",
            ))

    # New optional fields (low risk)
    for field_name in new_props:
        if field_name not in old_props and field_name not in new_required:
            items.append(APIChangeItem(
                file=file_path, line=0,
                change_type="added_optional_field",
                path=f"{context} → {field_name}",
                risk="low",
                reason=f"新增 optional 字段 {field_name}，向后兼容",
                suggestion="",
            ))

    return items
