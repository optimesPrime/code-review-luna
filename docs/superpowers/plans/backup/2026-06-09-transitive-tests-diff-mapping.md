# [后备计划 P1] Transitive 测试覆盖 + Diff→节点精准映射

**状态：** 后备，与 risk-scoring.md 配合实施（改进 F4 测试覆盖因子）

**价值：** ① Diff→节点映射：用行号区间精确定位到改动的函数，不再依赖 LLM 从 diff 文本推断；② Transitive 测试：检查"间接测试覆盖"，避免把"通过调用链被测试的函数"误报为无覆盖。

---

## 参考代码（精确位置）

| 功能 | 文件 | 行号 |
|------|------|------|
| git diff 解析（hunk header 提取行号区间） | `changes.py:15-75` | `parse_git_diff_ranges()` |
| SVN diff 解析 | `changes.py:77-130` | `parse_svn_diff_ranges()` |
| 行号区间 → 节点映射 | `changes.py:132-195` | `map_changes_to_nodes()` |
| 路径归一化（绝对↔相对↔suffix 匹配） | `tools/_common.py:78-130` | `_resolve_graph_file_paths()` |
| Transitive 测试查询 | `changes.py:197-255` | `get_transitive_tests()` |
| 5 因子风险评分（含 transitive tests） | `changes.py:257-340` | `compute_risk_score_for_node()` |
| 防 O(N·M) 截断 | `changes.py:355-399` | `analyze_changes()` 末段 |

**全路径前缀：** `/Users/wangyinlong/code-review-graph/code_review_graph/`

---

## 核心实现要点

### Diff → 行号区间解析
```python
def parse_git_diff_ranges(diff_text: str) -> dict[str, list[tuple[int, int]]]:
    file_pattern = re.compile(r"^\+\+\+ b/(.+)$", re.MULTILINE)
    # "@@ -42,3 +50,5 @@" → 新文件的 (50, 54) 区间
    hunk_pattern = re.compile(r"^@@ .+? \+(\d+)(?:,(\d+))? @@", re.MULTILINE)
    result = {}
    for file_match in file_pattern.finditer(diff_text):
        file_path = file_match.group(1)
        hunks = []
        for hunk in hunk_pattern.finditer(diff_text, file_match.start()):
            start = int(hunk.group(1))
            count = int(hunk.group(2) or 1)
            end = start + max(count - 1, 0)
            hunks.append((start, end))
        result[file_path] = hunks
    return result
```

### 行号区间 → 节点（函数/方法）
```python
def map_changes_to_nodes(store, ranges: dict[str, list[tuple[int, int]]]):
    for file_path, file_ranges in ranges.items():
        nodes = store.get_nodes_by_file(file_path)  # 先 exact match
        if not nodes:  # 再 suffix match（绝对路径兼容）
            nodes = store.get_nodes_by_file_suffix(file_path)
        for node in nodes:
            if node.kind == "File": continue
            for (start, end) in file_ranges:
                if node.line_start <= end and node.line_end >= start:
                    yield node  # 行号区间重叠 → 节点被改动
```

### Transitive 测试覆盖
```python
# 直接测试：TESTED_BY 入边
direct = {e.source for e in store.get_edges_by_target(node.qname)
          if e.kind == "TESTED_BY"}

# 间接测试：调用了"调用此节点的函数"的测试
callers = {e.source for e in store.get_edges_by_target(node.qname)
           if e.kind == "CALLS"}
indirect = set()
for caller in callers:
    for e in store.get_edges_by_target(caller):
        if e.kind == "TESTED_BY":
            indirect.add(e.source)

transitive_tests = direct | indirect
test_coverage_score = 1 - min(len(transitive_tests) / 5.0, 1.0)  # 5+ 测试 = 0 分
```

---

## Luna 实施要点

1. **替换 LLM 的"行号猜测"**：现在 blast_radius prompt 里有完整 diff，LLM 自己推断改了什么函数——改用 `map_changes_to_nodes` 精确定位，告诉 LLM"第 74 行的 `handleSubmit` 被改了"
2. **Suffix 匹配解决路径不一致**：diff 给的是 `src/views/Login.vue`，DB 里可能存的是 `/Users/xxx/project/src/views/Login.vue`，suffix 匹配解决这个问题
3. **Transitive tests 改进 F4 因子**：`risk-scoring.md` 的 F4 因子当前只看"有没有关联测试"，换成 transitive tests 后更准确
4. **Git ref 注入防护**：`^[A-Za-z0-9_.~^/@{}\-]+$` 正则校验 ref 参数，防注入（参考 `changes.py:15`）

---

## 估算

- Diff 解析：50 行
- 节点映射：30 行
- Transitive tests：40 行
- 路径归一化：50 行（可从 `_common.py` 直接复制）
- 工作量：3-5 天，可与 risk-scoring.md 合并实施
