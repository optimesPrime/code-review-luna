# ✅ 已完成 · SQLite 图存储 — 替换 context_graph.py

**目标：** 用 SQLite 替换现有基于 JSON 文件的 `context_graph.py`，一次解决三个问题：增量缓存（文件变了才重解析）、BFS 性能（RECURSIVE CTE 替代 Python deque）、内存占用（按需查询替代全量反序列化）。

**参考来源：** `/Users/wangyinlong/code-review-graph/code_review_graph/graph.py`（51KB）+ `incremental.py`（44KB）

---

## 核心改进对比

| 维度 | 现在（JSON 缓存） | 之后（SQLite） |
|------|-----------------|--------------|
| 500 文件改 1 个 | 全量重建 5-10s | 只重解析 1 个文件 <0.1s |
| 启动加载 | 反序列化全部 JSON | 不需要，直接查询 |
| BFS 影响链 | Python deque 逐节点 | 单条 RECURSIVE CTE |
| 缓存失效判断 | 无法判断 | SHA-256 精确比对 |
| 内存占用 | 全图常驻内存 | 按需查询 |

---

## Schema 设计

```sql
CREATE TABLE nodes (
    id          TEXT PRIMARY KEY,       -- "src/store/user.ts::useUserStore"
    node_type   TEXT NOT NULL,          -- "file" | "export" | "function" | "component"
    file        TEXT NOT NULL,
    name        TEXT NOT NULL,
    line        INTEGER DEFAULT 0,
    language    TEXT DEFAULT '',
    file_hash   TEXT DEFAULT ''         -- SHA-256，用于增量跳过
);

CREATE TABLE edges (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    source      TEXT NOT NULL,
    target      TEXT NOT NULL,
    edge_type   TEXT NOT NULL,          -- "imports" | "exports" | "calls"
    file        TEXT NOT NULL,
    line        INTEGER DEFAULT 0
);

CREATE INDEX idx_edges_source ON edges(source);
CREATE INDEX idx_edges_target ON edges(target);

-- FTS5 全文搜索（Task 4 混合检索使用）
CREATE VIRTUAL TABLE nodes_fts USING fts5(
    name, node_type, file,
    content=nodes, content_rowid=rowid,
    tokenize='porter unicode61'
);

CREATE TABLE schema_version (version INTEGER NOT NULL);
```

WAL 模式 + busy_timeout：
```python
conn.execute("PRAGMA journal_mode=WAL")
conn.execute("PRAGMA busy_timeout=5000")
```

---

## 架构

```
phases/sqlite_graph.py
  ├── class GraphDB                    # SQLite 连接封装
  │     ├── build(project_root)        # 全量扫描，带文件 hash 跳过
  │     ├── update(changed_files)      # 增量更新
  │     ├── get_importers(file) → list # 替换 _importers dict
  │     └── bfs_impact(files, depth)   # RECURSIVE CTE BFS
  └── 兼容层：build_graph / load_graph / save_graph 保持原接口
```

`context_graph.py` 的 `build_graph()` / `load_graph()` / `save_graph()` 接口保持不变，调用方（`luna.py`、`context_pack.py`）无需修改。

---

## 文件地图

| 操作 | 路径 | 职责 |
|------|------|------|
| Create | `phases/sqlite_graph.py` | GraphDB 类：schema、build、update、bfs_impact、get_importers |
| Modify | `phases/context_graph.py` | build_graph / load_graph / save_graph 委托给 GraphDB，保持接口 |
| Modify | `phases/risk_propagation.py` | propagate_risk 改用 GraphDB.bfs_impact，删除 Python deque |
| Modify | `config.py` | 新增 `graph_db_path` 配置项（默认 `.luna/cache/context-graph.db`） |
| Create | `tests/test_sqlite_graph.py` | 测试 build、增量 update、BFS |

---

## Task 1：GraphDB 基础——schema + build

**文件：** `phases/sqlite_graph.py`

- [ ] 写失败测试：
  - `test_build_creates_nodes_for_ts_files(tmp_path)`
  - `test_build_skips_node_modules(tmp_path)`
  - `test_node_has_file_hash(tmp_path)`
- [ ] 确认测试失败
- [ ] 实现 `GraphDB.__init__(db_path: str)`：创建连接，执行 schema，WAL + busy_timeout
- [ ] 实现 `GraphDB.build(project_root: str)`：
  - `rglob` 扫描 `.js/.ts/.jsx/.tsx/.vue` 文件
  - 对每个文件：`SHA-256(content)` → 若 `file_hash` 未变则跳过
  - 调用现有 `_process_js_file` / `_process_vue_file` 拿到 nodes/edges
  - `INSERT OR REPLACE INTO nodes` + `DELETE/INSERT INTO edges`
  - 批量事务（500 行/批）
- [ ] 确认测试通过
- [ ] `pytest -q` 全绿
- [ ] commit：`feat: GraphDB.build with SHA-256 incremental skip`

---

## Task 2：增量更新

**文件：** `phases/sqlite_graph.py`

- [ ] 写失败测试：
  - `test_update_only_reparses_changed_file(tmp_path)` — 改一个文件，另一个文件的节点不变
  - `test_update_removes_deleted_file_nodes(tmp_path)` — 文件删除后节点消失
- [ ] 确认测试失败
- [ ] 实现 `GraphDB.update(project_root: str)`：
  - 扫描全部文件，比对 `file_hash`
  - 变化的文件：删除其旧 nodes/edges，重新 parse 插入
  - 删除的文件：`DELETE FROM nodes WHERE file=?` + 对应 edges
- [ ] 实现 `GraphDB.is_fresh(project_root: str) -> bool`：快速判断是否需要 update
- [ ] 确认测试通过
- [ ] `pytest -q` 全绿
- [ ] commit：`feat: GraphDB.update — incremental re-parse on file change`

---

## Task 3：BFS via RECURSIVE CTE

**文件：** `phases/sqlite_graph.py`

- [ ] 写失败测试：
  - `test_bfs_impact_finds_two_hop_importers(tmp_path)` — A→B→C，从 A 出发找到 B 和 C
  - `test_bfs_impact_respects_max_depth(tmp_path)` — depth=1 只返回直接 importer
  - `test_bfs_impact_deduplicates(tmp_path)` — 环形依赖不死循环
- [ ] 确认测试失败
- [ ] 实现 `GraphDB.bfs_impact(seeds: list[str], max_depth: int = 3) -> list[dict]`：

```sql
WITH RECURSIVE impact(node, depth, path) AS (
    SELECT target, 1, target
    FROM edges
    WHERE source IN (/* seeds */)
    AND edge_type = 'imports'
    UNION ALL
    SELECT e.target, i.depth + 1, i.path || ',' || e.target
    FROM edges e
    JOIN impact i ON e.source = i.node
    WHERE i.depth < ?  -- max_depth
    AND instr(i.path, e.target) = 0  -- 去重防环
)
SELECT DISTINCT node, depth FROM impact ORDER BY depth
```

- [ ] 确认测试通过
- [ ] 实现 `GraphDB.get_importers(file: str) -> list[str]`：单文件直接 importer 查询
- [ ] `pytest -q` 全绿
- [ ] commit：`feat: GraphDB.bfs_impact via SQLite RECURSIVE CTE`

---

## Task 4：接入现有流程（兼容层）

**文件：** `phases/context_graph.py`、`phases/risk_propagation.py`

- [ ] 修改 `build_graph(project_root)` → 创建 `GraphDB`，调用 `update()`，返回包装对象
- [ ] 修改 `save_graph` / `load_graph` → 操作 `.db` 文件而非 `.json`
- [ ] 修改 `ContextGraph.find_usages(file)` → 委托给 `GraphDB.get_importers(file)`
- [ ] 修改 `risk_propagation.propagate_risk` → 改用 `GraphDB.bfs_impact`，删除 Python deque
- [ ] 写测试：`test_full_pipeline_uses_sqlite_graph` — 完整 build→propagate_risk 流程
- [ ] 确认测试通过
- [ ] `pytest -q` 全绿
- [ ] commit：`feat: wire GraphDB into context_graph and risk_propagation pipelines`

---

## Task 5：Schema 迁移机制

保证未来 schema 变化不会让旧 DB 文件出问题。

- [ ] 在 `sqlite_graph.py` 加 `_CURRENT_VERSION = 1`
- [ ] `GraphDB.__init__` 启动时检查 `schema_version`，版本不匹配则删库重建
- [ ] 写测试：`test_schema_version_mismatch_triggers_rebuild`
- [ ] 确认测试通过
- [ ] `pytest -q` 全绿
- [ ] commit：`feat: schema version check — auto-rebuild on mismatch`

---

## Task 6：验证

```bash
pytest -q
python3 luna.py --staged --phase blast  # 跑一次完整分析
```

手动验证：
```bash
# 在 luna 项目目录
luna --staged
# 修改一个 .ts 文件
luna --staged  # 应该比第一次快很多（只重解析修改的文件）
```

验收标准：
- 第二次运行明显比第一次快
- 修改 1 个文件后，只重解析该文件，其他文件 hash 不变
- BFS 结果与原 Python deque 版本一致
- 测试全绿

---

## Non-Goals（本阶段不做）

- 多仓库支持（registry）
- 向量嵌入（见混合检索计划书）
- Leiden 社区检测
- 后端语言（.cs/.java/.py 等）接入同一 SQLite，后端仍用独立 graph（见后端计划书）
