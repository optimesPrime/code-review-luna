# [后备计划 P2] Leiden 社区检测 — 自动模块发现

**状态：** 后备，sqlite-graph-store + hybrid-search-rrf 完成后实施

**价值：** 不依赖目录结构自动识别"逻辑模块"（支付域、鉴权域、订单域...），为 Surprise Scoring 提供真实社区信息（替换目录近似），为 architecture overview 提供跨模块耦合警告。

---

## 参考代码（精确位置）

| 功能 | 文件 | 行号 |
|------|------|------|
| Leiden 主路径（igraph） | `communities.py:236-310` | `_detect_leiden()` |
| Resolution 自适应公式 | `communities.py:267` | `1.0 / math.log10(max(n_nodes, 10))` |
| 边类型权重 | `communities.py:20-35` | `EDGE_WEIGHTS` |
| 文件级 fallback（无 igraph） | `communities.py:312-410` | `_detect_file_based()` |
| 内聚度批量计算 O(E) 而非 O(E·C) | `communities.py:412-490` | `_compute_cohesion_batch()` |
| 超大社区递归切分（>25% 总节点） | `communities.py:492-560` | `_split_oversized()` |
| 社区自动命名 | `communities.py:562-640` | `_generate_community_name()` |
| 跨社区耦合警告 | `communities.py:642-720` | `get_architecture_overview()` 后半段 |
| 测试社区过滤正则 | `communities.py:15` | `_TEST_COMMUNITY_RE` |

**全路径：** `/Users/wangyinlong/code-review-graph/code_review_graph/communities.py`

---

## 核心实现要点

### Resolution 自适应（关键经验值）
```python
# 大仓用更粗粒度，小仓用更细粒度
resolution = max(0.05, 1.0 / math.log10(max(n_nodes, 10)))
# 100 节点 → resolution=0.5，50 节点 → resolution=0.67
# 10000 节点 → resolution=0.25，100k 节点 → resolution=0.2
```

### 边权重（影响聚类结果）
```python
EDGE_WEIGHTS = {
    "CALLS": 1.0,        # 最强信号
    "INHERITS": 0.8,     # 继承关系
    "IMPLEMENTS": 0.7,
    "IMPORTS_FROM": 0.5,
    "DEPENDS_ON": 0.6,
    "TESTED_BY": 0.4,    # 测试关系权重低，避免测试文件干扰聚类
    "CONTAINS": 0.3,
}
```

### O(E) 批量内聚度计算
```python
# 关键优化：一次扫描所有边分桶，而非 per-community 扫描
qn_to_idx = {}  # qualified_name → community_index
for idx, members in enumerate(communities):
    for qn in members:
        qn_to_idx[qn] = idx
# 一次扫描
for edge in all_edges:
    sc = qn_to_idx.get(edge.source_qualified)
    tc = qn_to_idx.get(edge.target_qualified)
    if sc == tc:  # 内部边
        internal[sc] += 1
    else:         # 外部边
        if sc: external[sc] += 1
        if tc: external[tc] += 1
cohesion = internal[i] / (internal[i] + external[i] + 1e-9)
```

### 社区命名启发
```python
# 取所有成员文件路径的公共前缀最后一段
common = os.path.commonprefix([n.file_path for n in members])
purpose = common.rsplit('/', 1)[-1]  # "src/auth/..." → "auth"
# 加上最常见关键词（按字母频率）
name = f"{purpose}-{most_common_keyword}"  # e.g. "auth-token"
```

---

## Luna 实施要点

1. **igraph 为可选依赖**：`pyproject.toml` 加 `[communities]` extra，`pip install python-igraph`
2. **文件级 fallback 先实施**：不依赖 igraph，50 行代码，用目录路径聚类，可以先上线
3. **community_id 存入 nodes 表**：改 sqlite-graph-store 的 schema 加 `community_id` 外键
4. **Surprise Scoring 升级**：`surprise_analyzer.py` 的跨模块判断从"目录近似"升级为"真实 community_id 比对"
5. **RNG seed 固定 = 42**：保证相同代码库每次聚类结果一致（可通过环境变量覆盖）

---

## 估算

- 文件级 fallback：100 行，3 天
- Leiden 主路径（依赖 igraph）：200 行，1 周
- 命名 + 内聚度 + 切分：400 行，1 周
- 总工作量：2-3 周
