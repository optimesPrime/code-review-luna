from __future__ import annotations

import re
from phases.openapi_analyzer import APIChangeItem

# proto field: `  (repeated )? type name = number;`
_FIELD_RE = re.compile(
    r"^\s*(?:repeated\s+)?(\w+)\s+(\w+)\s*=\s*(\d+)\s*;",
)
# enum value: `  VALUE_NAME = number;`
_ENUM_RE = re.compile(r"^\s*([A-Z][A-Z0-9_]+)\s*=\s*(\d+)\s*;")


def _parse_fields(lines: list[str]) -> dict[str, tuple[str, int]]:
    """name → (type, number)"""
    result = {}
    for line in lines:
        if m := _FIELD_RE.match(line):
            ftype, fname, fnum = m.group(1), m.group(2), int(m.group(3))
            result[fname] = (ftype, fnum)
    return result


def _parse_enum_values(lines: list[str]) -> dict[str, int]:
    """enum_value_name → number"""
    result = {}
    for line in lines:
        if m := _ENUM_RE.match(line):
            result[m.group(1)] = int(m.group(2))
    return result


def analyze(diff: str, file_path: str) -> list[APIChangeItem]:
    """Parse a unified diff of a .proto file and return breaking changes."""
    if not file_path.endswith(".proto"):
        return []

    removed_lines: list[str] = []
    added_lines: list[str] = []

    for line in diff.split("\n"):
        if line.startswith("---") or line.startswith("+++"):
            continue
        if line.startswith("-"):
            removed_lines.append(line[1:])
        elif line.startswith("+"):
            added_lines.append(line[1:])

    if not removed_lines and not added_lines:
        return []

    old_fields = _parse_fields(removed_lines)
    new_fields = _parse_fields(added_lines)
    old_enums = _parse_enum_values(removed_lines)
    new_enums = _parse_enum_values(added_lines)

    items: list[APIChangeItem] = []

    # Build reverse lookup: field_number → (name, type) for new fields
    new_by_number: dict[int, tuple[str, str]] = {
        fnum: (fname, ftype) for fname, (ftype, fnum) in new_fields.items()
    }

    # Field number or type changed
    for fname, (old_type, old_num) in old_fields.items():
        if fname in new_fields:
            new_type, new_num = new_fields[fname]
            if old_num != new_num:
                items.append(APIChangeItem(
                    file=file_path, line=0,
                    change_type="changed_field_number",
                    path=fname,
                    risk="high",
                    reason=f"字段 {fname} 编号 {old_num} → {new_num}，二进制序列化不兼容",
                    suggestion="永远不要修改已发布字段的编号；用 reserved 保留旧编号",
                ))
            elif old_type != new_type:
                items.append(APIChangeItem(
                    file=file_path, line=0,
                    change_type="changed_field_type",
                    path=fname,
                    risk="high",
                    reason=f"字段 {fname} 类型 {old_type} → {new_type}，wire type 可能不兼容",
                    suggestion="保留旧字段，用新名称添加新类型字段",
                ))
        elif old_num in new_by_number:
            # Same field number exists under a different name → rename (binary compatible)
            new_fname, new_ftype = new_by_number[old_num]
            if old_type == new_ftype:
                items.append(APIChangeItem(
                    file=file_path, line=0,
                    change_type="renamed_field",
                    path=f"{fname} → {new_fname}",
                    risk="low",
                    reason=f"字段 {fname} 重命名为 {new_fname}，编号不变，二进制兼容",
                    suggestion="",
                ))
            else:
                items.append(APIChangeItem(
                    file=file_path, line=0,
                    change_type="changed_field_type",
                    path=fname,
                    risk="high",
                    reason=f"字段 {fname} 重命名并改类型 {old_type} → {new_ftype}，不兼容",
                    suggestion="保留旧字段，新增独立字段",
                ))
        else:
            # Field truly removed
            items.append(APIChangeItem(
                file=file_path, line=0,
                change_type="removed_field",
                path=fname,
                risk="high",
                reason=f"字段 {fname}（编号 {old_num}）被删除，旧消息反序列化时数据丢失",
                suggestion=f"用 reserved 保留字段编号和名称：reserved {old_num}; reserved \"{fname}\";",
            ))

    # New fields (low risk)
    for fname, (ftype, fnum) in new_fields.items():
        if fname not in old_fields:
            items.append(APIChangeItem(
                file=file_path, line=0,
                change_type="added_field",
                path=fname,
                risk="low",
                reason=f"新增字段 {fname}（编号 {fnum}），向后兼容",
                suggestion="",
            ))

    # Removed enum values
    for ename in old_enums:
        if ename not in new_enums:
            items.append(APIChangeItem(
                file=file_path, line=0,
                change_type="removed_enum_value",
                path=ename,
                risk="high",
                reason=f"枚举值 {ename} 被删除，旧数据反序列化将失败",
                suggestion="保留枚举值，用 DEPRECATED 后缀标记废弃",
            ))

    return items
