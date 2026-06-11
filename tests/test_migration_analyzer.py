from __future__ import annotations

import pytest
from phases.migration_analyzer import (
    MigrationRiskItem,
    detect_migration_files,
    analyze,
)

# ---------------------------------------------------------------------------
# detect_migration_files
# ---------------------------------------------------------------------------

def _diff_with_files(*paths: str) -> str:
    lines = []
    for p in paths:
        lines.append(f"diff --git a/{p} b/{p}")
        lines.append(f"index aaa..bbb 100644")
        lines.append(f"--- a/{p}")
        lines.append(f"+++ b/{p}")
        lines.append("@@ -1,1 +1,2 @@")
        lines.append("+-- change")
    return "\n".join(lines)


def test_detects_sql_migration_file():
    diff = _diff_with_files("migrations/V20__add_orders.sql")
    assert "migrations/V20__add_orders.sql" in detect_migration_files(diff)


def test_detects_plain_sql_file():
    diff = _diff_with_files("db/schema.sql")
    assert "db/schema.sql" in detect_migration_files(diff)


def test_detects_alembic_migration_file():
    diff = _diff_with_files("alembic/versions/abc123_add_col.py")
    assert "alembic/versions/abc123_add_col.py" in detect_migration_files(diff)


def test_detects_ef_core_migration_file():
    diff = _diff_with_files("Migrations/20240101_AddOrders.cs")
    assert "Migrations/20240101_AddOrders.cs" in detect_migration_files(diff)


def test_detects_django_migration_file():
    diff = _diff_with_files("orders/migrations/0003_add_amount.py")
    assert "orders/migrations/0003_add_amount.py" in detect_migration_files(diff)


def test_detects_laravel_migration_file():
    diff = _diff_with_files("database/migrations/2024_01_create_orders.php")
    assert "database/migrations/2024_01_create_orders.php" in detect_migration_files(diff)


def test_ignores_non_migration_file():
    diff = _diff_with_files("src/auth.ts", "README.md")
    assert detect_migration_files(diff) == []


# ---------------------------------------------------------------------------
# SQL DDL risk classification
# ---------------------------------------------------------------------------

def _sql_diff(sql_lines: list[str], file: str = "migrations/V1.sql") -> str:
    header = (
        f"diff --git a/{file} b/{file}\n"
        f"index aaa..bbb 100644\n"
        f"--- a/{file}\n"
        f"+++ b/{file}\n"
        f"@@ -1,1 +1,{len(sql_lines)} @@\n"
    )
    body = "\n".join(f"+{line}" for line in sql_lines)
    return header + body


def test_drop_column_is_high_risk():
    diff = _sql_diff(["ALTER TABLE orders DROP COLUMN amount;"])
    items = analyze(diff, ".")
    assert len(items) == 1
    assert items[0].risk == "high"
    assert "DROP" in items[0].operation.upper()


def test_drop_table_is_high_risk():
    diff = _sql_diff(["DROP TABLE legacy_orders;"])
    items = analyze(diff, ".")
    assert items[0].risk == "high"


def test_add_column_not_null_no_default_is_high_risk():
    diff = _sql_diff(["ALTER TABLE orders ADD COLUMN status VARCHAR(20) NOT NULL;"])
    items = analyze(diff, ".")
    assert items[0].risk == "high"


def test_add_column_not_null_with_default_is_medium_risk():
    diff = _sql_diff(["ALTER TABLE orders ADD COLUMN status VARCHAR(20) NOT NULL DEFAULT 'active';"])
    items = analyze(diff, ".")
    assert items[0].risk == "medium"


def test_add_column_null_is_low_risk():
    diff = _sql_diff(["ALTER TABLE orders ADD COLUMN note TEXT;"])
    items = analyze(diff, ".")
    assert items[0].risk == "low"


def test_create_index_without_concurrently_is_medium_risk():
    diff = _sql_diff(["CREATE INDEX idx_orders_user ON orders(user_id);"])
    items = analyze(diff, ".")
    assert items[0].risk == "medium"


def test_create_index_concurrently_is_low_risk():
    diff = _sql_diff(["CREATE INDEX CONCURRENTLY idx_orders_user ON orders(user_id);"])
    items = analyze(diff, ".")
    assert items[0].risk == "low"


def test_create_table_is_low_risk():
    diff = _sql_diff(["CREATE TABLE new_feature (id SERIAL PRIMARY KEY);"])
    items = analyze(diff, ".")
    assert items[0].risk == "low"


def test_rename_column_is_high_risk():
    diff = _sql_diff(["ALTER TABLE orders RENAME COLUMN amount TO total_amount;"])
    items = analyze(diff, ".")
    assert items[0].risk == "high"


def test_alter_column_type_is_high_risk():
    diff = _sql_diff(["ALTER TABLE orders ALTER COLUMN amount TYPE BIGINT;"])
    items = analyze(diff, ".")
    assert items[0].risk == "high"


def test_drop_index_is_low_risk():
    diff = _sql_diff(["DROP INDEX idx_orders_user;"])
    items = analyze(diff, ".")
    assert items[0].risk == "low"


def test_ignores_comment_lines():
    diff = _sql_diff(["-- DROP TABLE orders;", "CREATE TABLE safe (id INT);"])
    items = analyze(diff, ".")
    # The DROP inside a comment must not be flagged
    assert all("CREATE TABLE" in i.operation or i.risk == "low" for i in items)


# ---------------------------------------------------------------------------
# Alembic (Python) parsing
# ---------------------------------------------------------------------------

def _alembic_diff(py_lines: list[str]) -> str:
    file = "alembic/versions/abc_add_col.py"
    header = (
        f"diff --git a/{file} b/{file}\n"
        f"index aaa..bbb 100644\n"
        f"--- a/{file}\n"
        f"+++ b/{file}\n"
        f"@@ -1,1 +1,{len(py_lines)} @@\n"
    )
    body = "\n".join(f"+{line}" for line in py_lines)
    return header + body


def test_alembic_drop_column_is_high_risk():
    diff = _alembic_diff(["    op.drop_column('orders', 'amount')"])
    items = analyze(diff, ".")
    assert items[0].risk == "high"


def test_alembic_add_column_not_nullable_is_high_risk():
    diff = _alembic_diff([
        "    op.add_column('orders', sa.Column('status', sa.String(), nullable=False))"
    ])
    items = analyze(diff, ".")
    assert items[0].risk == "high"


def test_alembic_add_column_nullable_is_low_risk():
    diff = _alembic_diff([
        "    op.add_column('orders', sa.Column('note', sa.Text(), nullable=True))"
    ])
    items = analyze(diff, ".")
    assert items[0].risk == "low"


# ---------------------------------------------------------------------------
# EF Core (C#) parsing
# ---------------------------------------------------------------------------

def _ef_diff(cs_lines: list[str]) -> str:
    file = "Migrations/20240101_Init.cs"
    header = (
        f"diff --git a/{file} b/{file}\n"
        f"index aaa..bbb 100644\n"
        f"--- a/{file}\n"
        f"+++ b/{file}\n"
        f"@@ -1,1 +1,{len(cs_lines)} @@\n"
    )
    body = "\n".join(f"+{line}" for line in cs_lines)
    return header + body


def test_ef_drop_column_is_high_risk():
    diff = _ef_diff(['            migrationBuilder.DropColumn(name: "amount", table: "orders");'])
    items = analyze(diff, ".")
    assert items[0].risk == "high"


def test_ef_add_column_not_nullable_is_high_risk():
    diff = _ef_diff(['            migrationBuilder.AddColumn<string>(name: "status", table: "orders", nullable: false);'])
    items = analyze(diff, ".")
    assert items[0].risk == "high"


# ---------------------------------------------------------------------------
# Django migrations (Python) parsing
# ---------------------------------------------------------------------------

def _django_diff(py_lines: list[str]) -> str:
    file = "orders/migrations/0003_add_col.py"
    header = (
        f"diff --git a/{file} b/{file}\n"
        f"index aaa..bbb 100644\n"
        f"--- a/{file}\n"
        f"+++ b/{file}\n"
        f"@@ -1,1 +1,{len(py_lines)} @@\n"
    )
    body = "\n".join(f"+{line}" for line in py_lines)
    return header + body


def test_django_remove_field_is_high_risk():
    diff = _django_diff(["        migrations.RemoveField(model_name='order', name='amount'),"])
    items = analyze(diff, ".")
    assert items[0].risk == "high"


def test_django_add_field_not_null_no_default_is_high_risk():
    diff = _django_diff([
        "        migrations.AddField(model_name='order', name='status',"
        " field=models.CharField(max_length=20, null=False)),"
    ])
    items = analyze(diff, ".")
    assert items[0].risk == "high"


def test_no_items_for_non_migration_diff():
    diff = "diff --git a/src/auth.ts b/src/auth.ts\n+export function login() {}\n"
    items = analyze(diff, ".")
    assert items == []
