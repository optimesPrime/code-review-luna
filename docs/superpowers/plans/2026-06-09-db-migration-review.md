# 数据库迁移审查 — SQL / Migration 文件专项分析

**目标：** 自动检测 diff 中的数据库迁移文件变更，识别高风险操作（DROP、ADD NOT NULL、RENAME），无需 LLM 即可给出精确的风险判断，并在终端审查点矩阵中展示。

---

## 支持的迁移框架

| 框架 | 文件特征 |
|------|----------|
| 原始 SQL | `*.sql`、`V*.sql`（Flyway） |
| Alembic（Python） | `alembic/versions/*.py` |
| EF Core（C#） | `Migrations/*Migration.cs` |
| Django | `*/migrations/00*.py` |
| Laravel | `database/migrations/*.php` |

---

## 产品行为

终端审查点矩阵新增"数据库迁移"行：

```
  数据库迁移   🚨高   DROP COLUMN amount（不可逆）   V20_add_order.sql:12   manual
```

完整报告 Markdown 新增"数据库迁移"节：

```markdown
## 数据库迁移风险

### `DROP COLUMN amount` — `migrations/V20_add_order.sql:12`
- 风险: **high** · 不可逆操作
- 原因: 删除列会永久丢失数据，且无法回滚。需确认：1）已完成数据迁移；2）所有服务已不再读写此列。
- 建议: 先将列标记为废弃（重命名为 `_deprecated_amount`），观察一段时间后再删除。
```

---

## 风险分级规则

| 操作 | 风险 | 原因 |
|------|------|------|
| `DROP TABLE` | high | 不可逆，数据丢失 |
| `DROP COLUMN` | high | 不可逆，数据丢失 |
| `ADD COLUMN ... NOT NULL` 且无 `DEFAULT` | high | 现有行插入失败 |
| `RENAME TABLE` / `RENAME COLUMN` (SQL) | high | 破坏已有查询/ORM 映射 |
| `ALTER COLUMN` 改类型 | high | 隐式数据截断风险 |
| `ADD COLUMN ... NOT NULL DEFAULT ...` | medium | 全表锁，大表慢 |
| `CREATE INDEX` （非 `CONCURRENTLY`） | medium | 大表加索引会锁表 |
| `DROP INDEX` | low | 影响查询性能 |
| `ADD COLUMN NULL` | low | 安全，可向后兼容 |
| `CREATE TABLE` | low | 新增，无风险 |

---

## 架构

```
luna.py
  └── pipeline（检测到迁移文件时）
        └── migration_analyzer.py
              ├── detect_migration_files(diff) → list[MigrationFile]
              ├── parse_sql_operations(file_diff) → list[MigrationOp]
              ├── classify_risk(op) → MigrationRiskItem
              └── （可选）llm_explain(items, diff, cfg) → 补充建议
```

两阶段：
1. **静态分析**：正则/简单解析，不调 LLM，精确识别 DDL 操作和风险等级
2. **LLM 补充**（可选，`cfg.migration.use_llm=true`）：为 high 风险 item 生成详细建议和回滚方案

---

## 文件地图

| 操作 | 路径 | 职责 |
|------|------|------|
| Create | `phases/migration_analyzer.py` | 检测迁移文件；解析 DDL；分级；可选 LLM 补充 |
| Modify | `reporter.py` | `ReviewReport` 新增 `migration_items: list[MigrationRiskItem]` |
| Modify | `luna.py` | pipeline 中检测并调用 migration_analyzer |
| Modify | `terminal_renderer.py` | checkpoint 矩阵新增"数据库迁移"行 |
| Modify | `config.py` | 新增 `MigrationConfig(enabled=True, use_llm=False)` |
| Create | `tests/test_migration_analyzer.py` | 测试各种 DDL 操作的检测和分级 |

---

## 核心数据模型

```python
@dataclass
class MigrationRiskItem:
    file: str
    line: int
    operation: str      # "DROP COLUMN", "ADD COLUMN NOT NULL", ...
    table: str          # 涉及的表名
    column: str         # 涉及的列名（如适用）
    risk: str           # "high" | "medium" | "low"
    reason: str         # 风险说明
    suggestion: str     # 建议操作
    needs_human_review: bool = True
```

---

## Task 1：迁移文件检测与 DDL 解析

**文件：** `phases/migration_analyzer.py`

- [ ] 写失败测试（先写，覆盖各类迁移框架和 DDL 类型）：
  - `test_detects_sql_migration_file`
  - `test_detects_alembic_migration_file`
  - `test_detects_ef_core_migration_file`
  - `test_drop_column_is_high_risk`
  - `test_add_column_not_null_no_default_is_high_risk`
  - `test_add_column_null_is_low_risk`
  - `test_create_index_without_concurrently_is_medium_risk`
  - `test_create_table_is_low_risk`
- [ ] 确认测试失败
- [ ] 实现 `detect_migration_files(diff: str) -> list[str]`
  - 从 diff header 提取文件路径，按上表特征匹配
- [ ] 实现 `parse_sql_operations(file_path: str, diff_hunk: str) -> list[dict]`
  - 只解析 `+` 开头的新增行（改动引入的操作）
  - 正则匹配：`ALTER TABLE`、`DROP TABLE/COLUMN`、`ADD COLUMN`、`CREATE INDEX`、`RENAME`
  - 提取 table、column、是否含 `NOT NULL`、是否含 `DEFAULT`
- [ ] 实现 `classify_risk(op: dict) -> MigrationRiskItem` — 按风险分级规则表
- [ ] 实现 `analyze(diff: str, project_root: str) -> list[MigrationRiskItem]` — 串联以上逻辑
- [ ] 确认测试通过
- [ ] `pytest tests/test_migration_analyzer.py -v` 全绿
- [ ] commit：`feat: migration_analyzer — detect DDL operations and classify risk`

---

## Task 2：Alembic / EF Core / Django 解析适配

不同框架写法不同，需要额外解析逻辑：

- **Alembic**：`op.drop_column(...)`, `op.add_column(..., Column(..., nullable=False))`
- **EF Core**：`migrationBuilder.DropColumn(...)`, `migrationBuilder.AddColumn<T>(nullable: false)`
- **Django**：`migrations.RemoveField(...)`, `migrations.AddField(field=models.XXXField(null=False))`

- [ ] 写失败测试（各框架典型 diff 片段 → 正确识别操作）
- [ ] 确认测试失败
- [ ] 在 `parse_sql_operations` 中按文件扩展名路由到对应解析器
- [ ] 确认测试通过
- [ ] `pytest tests/test_migration_analyzer.py -v` 全绿
- [ ] commit：`feat: migration_analyzer — Alembic/EF Core/Django framework adapters`

---

## Task 3：接入主流程 + 终端渲染

**文件：** `luna.py`、`terminal_renderer.py`、`reporter.py`、`config.py`

- [ ] `config.py` 新增：

```python
@dataclass
class MigrationConfig:
    enabled: bool = True
    use_llm: bool = False
```

- [ ] `reporter.py` `ReviewReport` 新增 `migration_items: list = field(default_factory=list)`
- [ ] `luna.py` pipeline 末尾：若检测到迁移文件则调 `migration_analyzer.analyze(diff, ".")`，结果赋 `report.migration_items`
- [ ] `terminal_renderer.py` checkpoint 矩阵中：当 `report.migration_items` 非空时，在"数据库迁移"行展示最高风险 item；空时显示 `✅ 无迁移变更`
- [ ] 写测试：`test_migration_items_appear_in_checkpoint_matrix`
- [ ] 确认测试通过
- [ ] `pytest -q` 全绿
- [ ] commit：`feat: wire migration_analyzer into luna pipeline and checkpoint matrix`

---

## Task 4：可选 LLM 补充建议

仅在 `cfg.migration.use_llm=True` 时触发，为 high 风险 item 生成详细回滚方案。

- [ ] 实现 `llm_explain(items: list[MigrationRiskItem], cfg: Config) -> list[MigrationRiskItem]`
  - 只处理 `risk == "high"` 的 item
  - system prompt：资深 DBA 视角，给出可执行的回滚 SQL 和迁移建议
  - 结果更新 `item.suggestion`
- [ ] 写测试：`test_llm_explain_only_processes_high_risk`（mock api_client.call）
- [ ] 确认测试通过
- [ ] `pytest -q` 全绿
- [ ] commit：`feat: optional LLM explanation for high-risk migration items`

---

## Task 5：验证

```bash
pytest -q
```

手动验证（构造一个含 migration 文件改动的 diff）：

```bash
# 创建一个包含 DROP COLUMN 的 .sql 文件并暂存
luna --staged
```

验收标准：
- 终端审查点矩阵出现"数据库迁移"行，风险等级正确
- DROP COLUMN 标记 high，ADD COLUMN NULL 标记 low
- `use_llm=false` 时不调 LLM

---

## Non-Goals（本阶段不做）

- 自动生成回滚脚本并执行
- 分析迁移对现有数据量的性能影响（需要数据库连接）
- MongoDB / Redis schema 变更检测
