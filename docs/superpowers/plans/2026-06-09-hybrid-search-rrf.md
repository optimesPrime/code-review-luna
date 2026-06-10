# 混合检索 FTS5+向量+RRF — 爆炸范围语义增强

**目标：** 在 SQLite 图存储基础上加入全文检索（FTS5）和可选向量相似度，用 Reciprocal Rank Fusion 合并多路检索结果，让 Luna 的爆炸范围分析能找到"语义相关但没有直接依赖关系"的代码。

**参考来源：** `/Users/wangyinlong/code-review-graph/code_review_graph/search.py`（15KB，含完整 RRF 实现）+ `embeddings.py`（38KB，4 种 embedding provider）

**前置依赖：** `2026-06-09-sqlite-graph-store.md` 完成后执行（FTS5 表已在 SQLite schema 中）

---

## 现在的局限

Luna 的爆炸范围分析只走**依赖边 BFS**：`A import B，B import C → C 受影响`。

但以下情况会漏掉：
- 同名方法（不同文件的 `handleSubmit`，语义相同但没有 import 关系）
- 注释、配置文件里引用了改动的函数名
- 接口实现类（`implements IOrderService`，tree-sitter 不一定能解析出来）
- 测试文件（不被主代码 import，但测试改动的函数）

---

## 三路检索架构

```
changed_symbols
      ↓
┌──────────────┐   ┌──────────────┐   ┌──────────────────┐
│  路径 1：BFS  │   │ 路径 2：FTS5 │   │ 路径 3：向量相似 │
│  依赖边传播   │   │ 关键词全文   │   │ （可选，需配置） │
└──────┬───────┘   └──────┬───────┘   └────────┬─────────┘
       └─────────────┬────┘──────────────────── ┘
                     ↓
              RRF 合并（k=60）
                     ↓
              query kind boosting
                     ↓
            最终影响范围列表（带置信度）
```

**路径 1（BFS）**：已有实现，通过 `GraphDB.bfs_impact` 给出依赖链影响。
**路径 2（FTS5）**：用改动符号名做全文搜索，找到所有引用该名称的代码。
**路径 3（向量）**：可选，配置了 embedding provider 才启用，找语义相似的函数。

---

## RRF 算法（30 行核心代码）

```python
def rrf_merge(result_lists: list[list[str]], k: int = 60) -> list[tuple[str, float]]:
    """Reciprocal Rank Fusion — 合并多路排序结果."""
    scores: dict[str, float] = {}
    for results in result_lists:
        for rank, item in enumerate(results):
            scores[item] = scores.get(item, 0.0) + 1.0 / (k + rank + 1)
    return sorted(scores.items(), key=lambda x: x[1], reverse=True)
```

RRF 的好处：不需要归一化各路分数，直接按排名倒数求和，稳定可靠。

---

## Query Kind Boosting

根据改动符号名的格式自动加权：

```python
def detect_query_kind_boost(symbol: str) -> dict[str, float]:
    if symbol[0].isupper() and "_" not in symbol:  # PascalCase → 可能是 Class
        return {"class": 1.5, "component": 1.3}
    if "_" in symbol.lower():                        # snake_case → 可能是 Function
        return {"function": 1.5}
    if "." in symbol:                                # 含点 → qualified name 精确匹配
        return {"qualified": 2.0}
    return {}
```

---

## 文件地图

| 操作 | 路径 | 职责 |
|------|------|------|
| Create | `phases/hybrid_search.py` | FTS5 搜索、RRF 合并、query boosting |
| Create | `phases/embeddings.py`（可选） | 4-provider embedding 抽象（从 code-review-graph 移植） |
| Modify | `phases/sqlite_graph.py` | 在 build/update 时同步维护 `nodes_fts` 表 |
| Modify | `phases/context_pack.py` | 用 `hybrid_search` 替换纯 BFS 的影响文件列表 |
| Modify | `config.py` | 新增 `search.embedding_provider`（默认 `none`，可选 `local/openai`） |
| Create | `tests/test_hybrid_search.py` | 测试 FTS5、RRF 合并、boosting |

---

## Task 1：FTS5 搜索

**文件：** `phases/hybrid_search.py`（依赖 SQLite 图存储已建好）

- [ ] 写失败测试：
  - `test_fts_search_finds_symbol_by_name(tmp_path)` — 搜索 `handleSubmit` 能找到定义它的文件
  - `test_fts_search_case_insensitive(tmp_path)` — 大小写不敏感
  - `test_fts_search_returns_ranked_results(tmp_path)` — 结果按相关度排序
- [ ] 确认测试失败
- [ ] 实现 `fts_search(db: GraphDB, query: str, limit: int = 20) -> list[str]`：
  ```sql
  SELECT nodes.file, rank
  FROM nodes_fts
  JOIN nodes ON nodes_fts.rowid = nodes.rowid
  WHERE nodes_fts MATCH ?
  ORDER BY rank LIMIT ?
  ```
- [ ] 确认测试通过
- [ ] `pytest tests/test_hybrid_search.py -v` 通过
- [ ] commit：`feat: fts_search using SQLite FTS5`

---

## Task 2：RRF 合并 + Query Boosting

**文件：** `phases/hybrid_search.py`

- [ ] 写失败测试：
  - `test_rrf_merge_combines_two_lists` — 两路结果正确合并，交集项排名靠前
  - `test_rrf_merge_deduplicates` — 同一文件在两路中只出现一次
  - `test_detect_query_kind_boost_pascal_case` — `UserStore` → class boost
  - `test_detect_query_kind_boost_snake_case` — `get_user_by_id` → function boost
- [ ] 确认测试失败
- [ ] 实现 `rrf_merge`（30 行，见上方算法）
- [ ] 实现 `detect_query_kind_boost`
- [ ] 实现 `hybrid_search(db, symbols, max_depth=3) -> list[HybridResult]`：
  - 路径 1：`db.bfs_impact(seeds)` → 文件列表
  - 路径 2：对每个 symbol name 做 `fts_search` → 文件列表
  - `rrf_merge([bfs_results, fts_results])`
  - 按 `detect_query_kind_boost` 调整最终排序
- [ ] 确认测试通过
- [ ] `pytest tests/test_hybrid_search.py -v` 全绿
- [ ] commit：`feat: hybrid_search — BFS + FTS5 merged via RRF`

---

## Task 3：接入 context_pack

**文件：** `phases/context_pack.py`

- [ ] 修改 `build_context_pack`：用 `hybrid_search` 替换原来只走 BFS 的影响文件列表
- [ ] 对 `--phase blast` 的 LLM prompt 增加 FTS5 命中的额外文件，并标注"语义相关（非直接依赖）"
- [ ] 写测试：`test_context_pack_includes_fts_hits`
- [ ] 确认测试通过
- [ ] `pytest -q` 全绿
- [ ] commit：`feat: context_pack uses hybrid_search for broader impact coverage`

---

## Task 4（可选）：向量 Embedding Provider

**文件：** `phases/embeddings.py`

仅在用户配置了 `search.embedding_provider` 时启用。

- [ ] 从 `code-review-graph/code_review_graph/embeddings.py` 移植 4 种 provider 抽象：
  - `LocalEmbeddingProvider`（sentence-transformers，无需外部 API）
  - `OpenAIEmbeddingProvider`（兼容 OpenAI / Azure / 中转代理）
  - `GoogleEmbeddingProvider`
  - `MiniMaxEmbeddingProvider`
- [ ] 向量存 SQLite BLOB（`struct.pack(f"{n}f", *vec)`），不引入外部向量库
- [ ] `hybrid_search` 在 provider 可用时加入第三路向量检索
- [ ] 写测试：`test_local_embedding_provider_encodes_node`（mock sentence-transformers）
- [ ] commit：`feat: optional embedding providers for vector-augmented search`

---

## Task 5：验证

```bash
pytest -q
python3 luna.py --staged --phase blast
```

手动验证：
- 改动一个被其他文件"引用但不 import"的工具函数名，观察 FTS5 是否把它找出来
- 对比开启/关闭混合检索的影响范围列表差异

验收标准：
- FTS5 命中率：改动的函数名能在 90% 的情况下出现在搜索结果前 5 位
- RRF 合并结果不含重复文件
- 纯 BFS 路径不受影响（regression-free）
- 向量功能未配置时不报错，静默跳过

---

## Non-Goals（本阶段不做）

- 语义搜索替代 BFS（BFS 仍然是主路径，向量是补充）
- 实时 embedding 更新（向量在 build 时批量计算，不监听文件变化）
- 跨仓库语义搜索
