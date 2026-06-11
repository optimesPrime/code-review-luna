from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

# ── File detection patterns ──────────────────────────────────────────────────

_MIGRATION_PATTERNS = [
    re.compile(r"\.sql$", re.I),                          # raw SQL
    re.compile(r"alembic/versions/.*\.py$", re.I),        # Alembic
    re.compile(r"Migrations/.*Migration\.cs$"),            # EF Core
    re.compile(r"Migrations/.*\.cs$"),                     # EF Core (looser)
    re.compile(r".*/migrations/\d+.*\.py$", re.I),        # Django / generic
    re.compile(r"database/migrations/.*\.php$", re.I),    # Laravel
]

_DIFF_FILE_RE = re.compile(r"^diff --git a/\S+ b/(\S+)$", re.MULTILINE)


def detect_migration_files(diff: str) -> list[str]:
    """Return a list of migration file paths found in the diff."""
    found = []
    for m in _DIFF_FILE_RE.finditer(diff):
        path = m.group(1)
        if any(pat.search(path) for pat in _MIGRATION_PATTERNS):
            found.append(path)
    return found


# ── Data model ───────────────────────────────────────────────────────────────

@dataclass
class MigrationRiskItem:
    file: str
    line: int
    operation: str
    table: str
    column: str
    risk: str                   # "high" | "medium" | "low"
    reason: str
    suggestion: str
    needs_human_review: bool = True


# ── SQL DDL parser ───────────────────────────────────────────────────────────

# Patterns applied to individual added lines (stripped of leading '+')
_SQL_RULES: list[tuple[re.Pattern, callable]] = []


def _sql_rule(pattern: str):
    def decorator(fn):
        _SQL_RULES.append((re.compile(pattern, re.I | re.S), fn))
        return fn
    return decorator


@_sql_rule(r"DROP\s+TABLE\s+(?:IF\s+EXISTS\s+)?(\w+)")
def _drop_table(m, file, lineno):
    table = m.group(1)
    return MigrationRiskItem(
        file=file, line=lineno, operation="DROP TABLE", table=table, column="",
        risk="high", reason=f"DROP TABLE {table} 不可逆，数据将永久丢失",
        suggestion="确认数据已备份或迁移；先重命名观察后再删除",
    )


@_sql_rule(r"ALTER\s+TABLE\s+(\w+)\s+DROP\s+COLUMN\s+(\w+)")
def _drop_column(m, file, lineno):
    table, col = m.group(1), m.group(2)
    return MigrationRiskItem(
        file=file, line=lineno, operation="DROP COLUMN", table=table, column=col,
        risk="high", reason=f"{table}.{col} 列删除不可逆，数据丢失",
        suggestion="先将列标记废弃（重命名为 _deprecated_{col}），确认无读写后再删除",
    )


@_sql_rule(r"ALTER\s+TABLE\s+(\w+)\s+RENAME\s+(?:COLUMN\s+)?(\w+)\s+TO\s+(\w+)")
def _rename_column(m, file, lineno):
    table, old_col, new_col = m.group(1), m.group(2), m.group(3)
    return MigrationRiskItem(
        file=file, line=lineno, operation="RENAME COLUMN", table=table, column=old_col,
        risk="high", reason=f"{table}.{old_col} → {new_col}，破坏已有查询和 ORM 映射",
        suggestion="滚动发布：先加新列，双写，迁移数据，再删旧列",
    )


@_sql_rule(r"ALTER\s+TABLE\s+(\w+)\s+(?:ALTER|MODIFY)\s+COLUMN\s+(\w+)\s+(\w+)")
def _alter_column_type(m, file, lineno):
    table, col, new_type = m.group(1), m.group(2), m.group(3)
    return MigrationRiskItem(
        file=file, line=lineno, operation="ALTER COLUMN TYPE", table=table, column=col,
        risk="high", reason=f"{table}.{col} 类型改为 {new_type}，可能隐式截断数据",
        suggestion="先验证现有数据兼容新类型，必要时在应用层做格式转换",
    )


@_sql_rule(r"ALTER\s+TABLE\s+(\w+)\s+ADD\s+COLUMN\s+(\w+)\s+\w+.*NOT\s+NULL(?!.*DEFAULT)")
def _add_column_not_null_no_default(m, file, lineno):
    table, col = m.group(1), m.group(2)
    return MigrationRiskItem(
        file=file, line=lineno, operation="ADD COLUMN NOT NULL", table=table, column=col,
        risk="high", reason=f"{table}.{col} 新增 NOT NULL 列但无 DEFAULT，现有行插入将报错",
        suggestion="先加 DEFAULT 值，待全量迁移后再去掉 DEFAULT",
    )


@_sql_rule(r"ALTER\s+TABLE\s+(\w+)\s+ADD\s+COLUMN\s+(\w+)\s+\w+.*NOT\s+NULL.*DEFAULT")
def _add_column_not_null_with_default(m, file, lineno):
    table, col = m.group(1), m.group(2)
    return MigrationRiskItem(
        file=file, line=lineno, operation="ADD COLUMN NOT NULL DEFAULT", table=table, column=col,
        risk="medium", reason=f"{table}.{col} 新增 NOT NULL+DEFAULT，大表会全表锁",
        suggestion="大表建议分批迁移或使用 pt-online-schema-change",
    )


@_sql_rule(r"ALTER\s+TABLE\s+(\w+)\s+ADD\s+COLUMN\s+(\w+)")
def _add_column_nullable(m, file, lineno):
    table, col = m.group(1), m.group(2)
    return MigrationRiskItem(
        file=file, line=lineno, operation="ADD COLUMN", table=table, column=col,
        risk="low", reason=f"{table}.{col} 新增可空列，向后兼容",
        suggestion="",
    )


@_sql_rule(r"CREATE\s+INDEX\s+CONCURRENTLY\s+(\w+)")
def _create_index_concurrently(m, file, lineno):
    idx = m.group(1)
    return MigrationRiskItem(
        file=file, line=lineno, operation="CREATE INDEX CONCURRENTLY", table="", column="",
        risk="low", reason=f"索引 {idx} 以 CONCURRENTLY 方式创建，不锁表",
        suggestion="",
    )


@_sql_rule(r"CREATE\s+INDEX\s+(\w+)")
def _create_index(m, file, lineno):
    idx = m.group(1)
    return MigrationRiskItem(
        file=file, line=lineno, operation="CREATE INDEX", table="", column="",
        risk="medium", reason=f"索引 {idx} 创建期间会锁表，大表耗时较长",
        suggestion="生产环境建议使用 CREATE INDEX CONCURRENTLY",
    )


@_sql_rule(r"DROP\s+INDEX\s+(\w+)")
def _drop_index(m, file, lineno):
    idx = m.group(1)
    return MigrationRiskItem(
        file=file, line=lineno, operation="DROP INDEX", table="", column="",
        risk="low", reason=f"删除索引 {idx} 会影响依赖该索引的查询性能",
        suggestion="确认无慢查询依赖后再删除",
    )


@_sql_rule(r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?(\w+)")
def _create_table(m, file, lineno):
    table = m.group(1)
    return MigrationRiskItem(
        file=file, line=lineno, operation="CREATE TABLE", table=table, column="",
        risk="low", reason=f"新建表 {table}，无破坏性",
        suggestion="",
    )


def _parse_sql(diff_hunk: str, file_path: str) -> list[MigrationRiskItem]:
    items: list[MigrationRiskItem] = []
    for lineno, raw in enumerate(diff_hunk.split("\n"), start=1):
        if not raw.startswith("+") or raw.startswith("+++"):
            continue
        line = raw[1:].strip()
        if line.startswith("--"):      # SQL comment
            continue
        for pat, fn in _SQL_RULES:
            m = pat.search(line)
            if m:
                items.append(fn(m, file_path, lineno))
                break
    return items


# ── Alembic (Python) parser ──────────────────────────────────────────────────

_ALEMBIC_DROP = re.compile(r"op\.drop_column\(\s*['\"](\w+)['\"]\s*,\s*['\"](\w+)['\"]")
_ALEMBIC_ADD = re.compile(r"op\.add_column\(\s*['\"](\w+)['\"]\s*,\s*.*Column\(.*nullable\s*=\s*(False|True)", re.I)
_ALEMBIC_DROP_TABLE = re.compile(r"op\.drop_table\(\s*['\"](\w+)['\"]")
_ALEMBIC_RENAME = re.compile(r"op\.alter_column\(.*new_column_name")


def _parse_alembic(diff_hunk: str, file_path: str) -> list[MigrationRiskItem]:
    items: list[MigrationRiskItem] = []
    for lineno, raw in enumerate(diff_hunk.split("\n"), start=1):
        if not raw.startswith("+") or raw.startswith("+++"):
            continue
        line = raw[1:]

        if m := _ALEMBIC_DROP_TABLE.search(line):
            items.append(MigrationRiskItem(
                file=file_path, line=lineno, operation="DROP TABLE",
                table=m.group(1), column="", risk="high",
                reason=f"op.drop_table('{m.group(1)}') 不可逆",
                suggestion="确认数据已迁移后再执行",
            ))
        elif m := _ALEMBIC_DROP.search(line):
            items.append(MigrationRiskItem(
                file=file_path, line=lineno, operation="DROP COLUMN",
                table=m.group(1), column=m.group(2), risk="high",
                reason=f"op.drop_column('{m.group(1)}', '{m.group(2)}') 不可逆",
                suggestion="先标记废弃，确认无读写后再删除",
            ))
        elif m := _ALEMBIC_ADD.search(line):
            nullable = m.group(2).lower() != "false"
            risk = "low" if nullable else "high"
            reason = (
                "新增 NOT NULL 列无 DEFAULT，现有行插入报错" if not nullable
                else "新增可空列，向后兼容"
            )
            table_m = re.search(r"op\.add_column\(\s*['\"](\w+)['\"]", line)
            table = table_m.group(1) if table_m else ""
            col_m = re.search(r"Column\(\s*['\"](\w+)['\"]", line)
            col = col_m.group(1) if col_m else ""
            items.append(MigrationRiskItem(
                file=file_path, line=lineno, operation="ADD COLUMN",
                table=table, column=col, risk=risk, reason=reason,
                suggestion="" if nullable else "先设置 server_default，迁移后再去掉",
            ))
        elif _ALEMBIC_RENAME.search(line):
            items.append(MigrationRiskItem(
                file=file_path, line=lineno, operation="RENAME COLUMN",
                table="", column="", risk="high",
                reason="alter_column 重命名破坏已有 ORM 映射",
                suggestion="滚动发布：双写新旧列，迁移完成后删旧列",
            ))
    return items


# ── EF Core (C#) parser ──────────────────────────────────────────────────────

_EF_DROP_COL = re.compile(r"migrationBuilder\.DropColumn\(.*?name:\s*\"(\w+)\".*?table:\s*\"(\w+)\"", re.I)
_EF_DROP_TABLE = re.compile(r"migrationBuilder\.DropTable\(.*?name:\s*\"(\w+)\"", re.I)
_EF_ADD_COL = re.compile(r"migrationBuilder\.AddColumn<.*?>\(.*?name:\s*\"(\w+)\".*?table:\s*\"(\w+)\"(.*)", re.I | re.S)


def _parse_ef(diff_hunk: str, file_path: str) -> list[MigrationRiskItem]:
    items: list[MigrationRiskItem] = []
    for lineno, raw in enumerate(diff_hunk.split("\n"), start=1):
        if not raw.startswith("+") or raw.startswith("+++"):
            continue
        line = raw[1:]

        if m := _EF_DROP_TABLE.search(line):
            items.append(MigrationRiskItem(
                file=file_path, line=lineno, operation="DROP TABLE",
                table=m.group(1), column="", risk="high",
                reason=f"DropTable '{m.group(1)}' 不可逆",
                suggestion="确认数据已迁移",
            ))
        elif m := _EF_DROP_COL.search(line):
            items.append(MigrationRiskItem(
                file=file_path, line=lineno, operation="DROP COLUMN",
                table=m.group(2), column=m.group(1), risk="high",
                reason=f"DropColumn '{m.group(1)}' 在表 '{m.group(2)}' 不可逆",
                suggestion="先标记废弃后再删除",
            ))
        elif m := _EF_ADD_COL.search(line):
            col, table, rest = m.group(1), m.group(2), m.group(3)
            nullable = "nullable: false" not in rest.lower()
            risk = "low" if nullable else "high"
            items.append(MigrationRiskItem(
                file=file_path, line=lineno, operation="ADD COLUMN",
                table=table, column=col, risk=risk,
                reason=(
                    f"AddColumn '{col}' nullable:false 无 default，旧行插入报错"
                    if not nullable else f"AddColumn '{col}' 可空，安全"
                ),
                suggestion="" if nullable else "先设置 DefaultValue，迁移后再去掉",
            ))
    return items


# ── Django migrations parser ─────────────────────────────────────────────────

_DJ_REMOVE = re.compile(r"migrations\.RemoveField\(.*?model_name=['\"](\w+)['\"].*?name=['\"](\w+)['\"]", re.I | re.S)
_DJ_ADD = re.compile(r"migrations\.AddField\(.*?model_name=['\"](\w+)['\"].*?name=['\"](\w+)['\"].*?field=.*?null\s*=\s*(True|False)", re.I | re.S)
_DJ_DELETE_MODEL = re.compile(r"migrations\.DeleteModel\(.*?name=['\"](\w+)['\"]", re.I | re.S)


def _parse_django(diff_hunk: str, file_path: str) -> list[MigrationRiskItem]:
    items: list[MigrationRiskItem] = []
    full_added = "\n".join(
        l[1:] for l in diff_hunk.split("\n")
        if l.startswith("+") and not l.startswith("+++")
    )

    for m in _DJ_DELETE_MODEL.finditer(full_added):
        items.append(MigrationRiskItem(
            file=file_path, line=0, operation="DROP TABLE",
            table=m.group(1), column="", risk="high",
            reason=f"DeleteModel '{m.group(1)}' 不可逆",
            suggestion="确认所有关联数据已迁移",
        ))

    for m in _DJ_REMOVE.finditer(full_added):
        items.append(MigrationRiskItem(
            file=file_path, line=0, operation="DROP COLUMN",
            table=m.group(1), column=m.group(2), risk="high",
            reason=f"RemoveField '{m.group(1)}.{m.group(2)}' 不可逆",
            suggestion="先标记废弃后再删除",
        ))

    for m in _DJ_ADD.finditer(full_added):
        model, name, null_val = m.group(1), m.group(2), m.group(3)
        nullable = null_val.lower() == "true"
        risk = "low" if nullable else "high"
        items.append(MigrationRiskItem(
            file=file_path, line=0, operation="ADD COLUMN",
            table=model, column=name, risk=risk,
            reason=(
                f"AddField '{model}.{name}' null=False 无默认值，旧行插入报错"
                if not nullable else f"AddField '{model}.{name}' 可空，安全"
            ),
            suggestion="" if nullable else "先设置 default=，迁移后再去掉",
        ))

    return items


# ── File router ──────────────────────────────────────────────────────────────

def _get_file_diff(diff: str, file_path: str) -> str:
    """Extract the diff hunk block for a specific file."""
    marker = f"diff --git a/{file_path} b/{file_path}"
    start = diff.find(marker)
    if start == -1:
        return ""
    end = diff.find("\ndiff --git ", start + 1)
    return diff[start:] if end == -1 else diff[start:end]


def _parse_file(file_path: str, diff_hunk: str) -> list[MigrationRiskItem]:
    ext = Path(file_path).suffix.lower()
    name = Path(file_path).name.lower()

    if ext == ".sql":
        return _parse_sql(diff_hunk, file_path)
    if ext == ".py":
        if "alembic" in file_path.lower():
            return _parse_alembic(diff_hunk, file_path)
        return _parse_django(diff_hunk, file_path)
    if ext == ".cs":
        return _parse_ef(diff_hunk, file_path)
    if ext == ".php":
        return _parse_sql(diff_hunk, file_path)  # Laravel uses raw SQL in migration files
    return []


# ── Public API ───────────────────────────────────────────────────────────────

def analyze(diff: str, project_root: str) -> list[MigrationRiskItem]:
    """Detect migration files in the diff and classify DDL risk operations."""
    migration_files = detect_migration_files(diff)
    items: list[MigrationRiskItem] = []
    for f in migration_files:
        hunk = _get_file_diff(diff, f)
        items.extend(_parse_file(f, hunk))
    return items
