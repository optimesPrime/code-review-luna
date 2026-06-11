from __future__ import annotations

from phases.proto_analyzer import analyze, APIChangeItem


def _diff(removed: list[str], added: list[str], file: str = "api.proto") -> str:
    lines = [
        f"diff --git a/{file} b/{file}",
        "index aaa..bbb 100644",
        f"--- a/{file}",
        f"+++ b/{file}",
        "@@ -1,10 +1,10 @@",
    ]
    for l in removed:
        lines.append(f"-{l}")
    for l in added:
        lines.append(f"+{l}")
    return "\n".join(lines)


def test_detects_field_number_change():
    items = analyze(
        _diff(
            ["  string name = 1;"],
            ["  string name = 2;"],
        ),
        "api.proto",
    )
    assert any(i.change_type == "changed_field_number" for i in items)
    assert any(i.risk == "high" for i in items)


def test_detects_field_type_change():
    items = analyze(
        _diff(
            ["  string amount = 1;"],
            ["  int64  amount = 1;"],
        ),
        "api.proto",
    )
    assert any(i.change_type == "changed_field_type" for i in items)
    assert any(i.risk == "high" for i in items)


def test_detects_removed_field():
    items = analyze(
        _diff(
            ["  string deprecated_field = 5;"],
            [],
        ),
        "api.proto",
    )
    assert any(i.change_type == "removed_field" for i in items)
    assert any(i.risk == "high" for i in items)


def test_detects_enum_value_removal():
    items = analyze(
        _diff(
            ["  STATUS_PENDING = 2;"],
            [],
        ),
        "api.proto",
    )
    assert any(i.change_type == "removed_enum_value" for i in items)
    assert any(i.risk == "high" for i in items)


def test_field_rename_is_low_risk():
    # Same number, different name
    items = analyze(
        _diff(
            ["  string old_name = 3;"],
            ["  string new_name = 3;"],
        ),
        "api.proto",
    )
    # Rename only → low risk (binary compatible)
    assert all(i.risk == "low" for i in items)


def test_new_field_is_low_risk():
    items = analyze(
        _diff(
            [],
            ["  string new_field = 10;"],
        ),
        "api.proto",
    )
    assert all(i.risk == "low" for i in items)


def test_returns_empty_for_no_proto_changes():
    items = analyze("diff --git a/src/app.ts b/src/app.ts\n+const x = 1;", "src/app.ts")
    assert items == []
