# [后备计划 P1] 执行流追踪 + 框架入口检测

**状态：** 后备，待前置计划（sqlite-graph-store）完成后实施

**价值：** 识别框架入口点（Spring/FastAPI/NestJS/React...），追踪调用链，计算 criticality，让 PR review 能直接回答"这次改动影响了哪些关键执行流，各流的风险有多高"。

---

## 参考代码（精确位置）

| 功能 | 文件 | 行号 |
|------|------|------|
| Entry point 检测（三重判定：无入边/框架装饰器/命名约定） | `flows.py:89-187` | `detect_entry_points()` |
| 27 条框架装饰器正则 | `flows.py:25-65` | `_FRAMEWORK_DECORATOR_PATTERNS` |
| 约定命名正则 | `flows.py:67-86` | `_ENTRY_NAME_PATTERNS` |
| BFS 单流追踪 | `flows.py:229-297` | `_trace_single_flow()` |
| FlowAdjacency 预加载 | `graph.py:113-158` | `class FlowAdjacency` |
| Criticality 评分（5 因子） | `flows.py:299-380` | `_compute_criticality()` |
| 增量重新追踪 | `flows.py:435-550` | `incremental_trace_flows()` |
| 受影响流查询 | `tools/review.py:225-320` | `get_affected_flows()` |

**全路径前缀：** `/Users/wangyinlong/code-review-graph/code_review_graph/`

---

## 核心实现要点

### Entry Point 三重判定
```python
# 1. 图论根（无 CALLS 入边）
called_qnames = {e.target_qualified for e in all_calls_edges}
if node.qualified_name not in called_qnames:
    entry_points.add(node)

# 2. 框架装饰器（27 条正则）
_FRAMEWORK_DECORATOR_PATTERNS = [
    r"@app\.(get|post|put|delete|patch)",  # FastAPI/Flask
    r"@router\.(get|post|put|delete)",     # FastAPI router
    r"@(Get|Post|Put|Delete|Patch)\(",     # NestJS/Spring
    r"@Controller\b", r"@RestController\b",
    r"@click\.command", r"@cli\.(command|group)",
    r"@celery\.task", r"@shared_task",
    r"@pytest\.fixture",
    # ... 共 27 条
]

# 3. 命名约定
_ENTRY_NAME_PATTERNS = [
    "main", "handler", "lambda_handler",
    r"test_.*", r"on_.*", r"handle_.*",
    "upgrade", "downgrade",  # alembic migration
    "do_GET", "do_POST",     # BaseHTTPRequestHandler
    "ngOnInit", "componentDidMount", "canActivate",
]
```

### Criticality 5 因子
```python
criticality = (
    min(file_spread / 5, 1.0)     * 0.30  # 跨多少文件
  + min(external_calls / 5, 1.0)  * 0.20  # 调用外部库次数
  + security_ratio                 * 0.25  # 命中 SECURITY_KEYWORDS 节点比例
  + test_gap_ratio                 * 0.15  # 1 - 测试覆盖率
  + min(max_depth / 10, 1.0)      * 0.10  # BFS 最大深度
)
```

### 增量更新的关键逻辑
1. 先收集受影响 flow 的旧 entry_point_id
2. 事务删除受影响 flow + memberships
3. 重跑 entry point 检测（只处理 changed_files 范围）
4. BFS 追踪 + 写入新 flow
5. 未受影响的 flow 完全不动

---

## Luna 实施要点

1. **FlowAdjacency 一次性加载**：BFS 前把所有 CALLS 边加载到内存 dict，避免 per-step DB 查询
2. **27 条装饰器正则**：可从参考代码直接复制，已覆盖 Luna 现有的所有 7 种后端语言
3. **前端框架**：参考代码已有 React（`componentDidMount`）和 Angular（`ngOnInit`），可扩展 Vue（`mounted`、`setup`）
4. **criticality 存入 SQLite**：`flows` 表，供 blast_radius 优先排序使用

---

## 估算

- 核心实现：800-1000 行 Python
- 工作量：2 周
- 前置：`sqlite-graph-store.md` 完成（需要 SQLite flows 表）
