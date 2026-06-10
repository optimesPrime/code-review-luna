# Surprise Scoring + 自动 Review 问题生成

**目标：** 两件事合一：① 给爆炸范围里的每条影响路径打"可疑耦合分数"，自动发现架构异味；② 把图分析信号翻译成自然语言问题，直接作为 LLM 审查的引导上下文，让 LLM 专注回答而不是自己发现问题。

**参考代码：**
- `analysis.py:158-275` — `find_surprising_connections`（Surprise scoring 核心，5 条规则 + 30 行）
- `analysis.py:277-411` — `generate_suggested_questions`（问题生成，信号 → 自然语言）
- `analysis.py:1-157` — `find_hub_nodes`、`find_bridge_nodes`、`find_knowledge_gaps`（输入信号）
- 全路径：`/Users/wangyinlong/code-review-graph/code_review_graph/analysis.py`

---

## 产品效果

Luna 终端输出新增"审查关注点"节，在 LLM 分析之前展示：

```
🔍 自动发现的审查关注点

⚡ 可疑耦合（2 处）
  • request.ts → auth.ts（跨模块：请求层直接调用鉴权层，是有意的吗？）
  • Login.vue → order.ts（跨语言：前端组件直接依赖业务域，建议通过 store 解耦）

🧪 测试覆盖盲区（1 处）
  • handleSubmit — 有 8 个调用者但无测试覆盖

🌉 关键桥接节点（1 处）
  • request.ts — 多个模块的唯一连接器，修改影响全局
```

这些问题直接注入 blast_radius 的 LLM prompt，让 LLM 直接回答"是有意的吗""风险在哪"，而不是从头推断。

---

## Surprise Scoring 规则（5 条）

```python
score = 0.0
if src_community != tgt_community:          score += 0.30  # 跨模块
if src_language != tgt_language:            score += 0.20  # 跨语言
if src_degree <= 2 and tgt_degree >= 3×median: score += 0.20  # 边缘→枢纽
if src_is_test != tgt_is_test:              score += 0.15  # 跨测试边界
if edge_type == "CALLS" and src_kind == "Type": score += 0.15  # 不合理边类型
```

threshold = 0.35 → 标记为可疑。

**注意**：社区信息在 SQLite 图存储（`sqlite-graph-store.md`）落地之前，用目录前缀近似：同目录 = 同社区，跨目录 = 跨社区。

---

## 问题生成规则（7 类信号）

| 信号 | 生成的问题 |
|------|------------|
| surprise_score > 0.35 | "X（模块A）调用了 Y（模块B），是跨模块依赖，是有意的吗？" |
| hub_node（度数 top 5%） | "X 有 N 个连接，是核心枢纽，这次改动影响范围大" |
| bridge_node（中介中心性高） | "X 是多个模块的唯一连接器，改动需谨慎" |
| untested_hotspot（度数≥5 无测试） | "X 有 N 个调用者但没有测试覆盖" |
| cross_language_edge | "X（前端）直接依赖 Y（后端），建议通过 API 层解耦" |
| test→production CALLS | "测试文件直接调用了生产代码，而不是通过公共接口" |
| thin_impact（影响 < 2 个文件） | （不生成问题，此改动影响范围小，无需特别关注） |

每类最多生成 3 个问题，总问题数上限 7 个，避免问题列表过长淹没重点。

---

## 架构

```
phases/surprise_analyzer.py（新增）
  ├── compute_surprise_score(edge, graph_context) → float
  ├── find_surprising_edges(impact_paths, graph) → list[SurpriseEdge]
  ├── find_untested_hotspots(changed_symbols, graph) → list[str]
  ├── find_bridge_nodes_in_impact(impact_paths, graph) → list[str]
  └── generate_review_questions(surprise_edges, hotspots, bridges) → list[str]

phases/blast_radius.py（修改）
  └── analyze() prompt 中注入 generate_review_questions 的结果

terminal_renderer.py（修改）
  └── 审查点矩阵新增"可疑耦合"和"关注点"展示
```

---

## 文件地图

| 操作 | 路径 | 职责 |
|------|------|------|
| Create | `phases/surprise_analyzer.py` | Surprise scoring + 问题生成 |
| Modify | `phases/context_pack.py` | 把生成的问题加入 context pack |
| Modify | `phases/blast_radius.py` | LLM prompt 注入 review 问题 |
| Modify | `terminal_renderer.py` | 展示可疑耦合和关注点 |
| Modify | `reporter.py` | `ReviewReport` 新增 `review_questions: list[str]` |
| Create | `tests/test_surprise_analyzer.py` | 测试各规则和问题生成 |

---

## Task 1：Surprise Scoring 核心

**文件：** `phases/surprise_analyzer.py`

- [ ] 写失败测试：
  - `test_cross_module_edge_scores_high` — 跨目录边 score > 0.3
  - `test_same_module_edge_scores_low` — 同目录边 score < 0.1
  - `test_cross_language_adds_to_score` — `.ts` 调 `.py` score += 0.2
  - `test_score_threshold_marks_suspicious` — score > 0.35 → is_suspicious=True
- [ ] 确认测试失败
- [ ] 实现 `@dataclass SurpriseEdge(source, target, score, reasons: list[str])`
- [ ] 实现 `compute_surprise_score(source_file, target_file, edge_type, graph_context) -> tuple[float, list[str]]`：
  - `graph_context` 是轻量字典：`{file: {"community": str, "language": str, "degree": int, "is_test": bool}}`
  - 无社区信息时用目录前缀近似（`os.path.dirname`）
  - 无度数信息时跳过边缘→枢纽规则
- [ ] 实现 `find_surprising_edges(impact_paths, graph_context, threshold=0.35) -> list[SurpriseEdge]`
- [ ] 确认测试通过
- [ ] `pytest tests/test_surprise_analyzer.py -v` 通过
- [ ] commit：`feat: surprise_analyzer — cross-module/language/hub edge scoring`

---

## Task 2：无测试热点 + 桥接节点检测

**文件：** `phases/surprise_analyzer.py`

- [ ] 写失败测试：
  - `test_hotspot_has_high_degree_no_tests` — 度数 ≥ 5，related_tests 为空 → 进热点列表
  - `test_hotspot_with_tests_not_flagged` — 有测试 → 不进热点列表
  - `test_bridge_node_single_connector` — A→X→B，X 是唯一桥接 → 进桥接列表
- [ ] 确认测试失败
- [ ] 实现 `find_untested_hotspots(changed_symbols, related_tests, min_degree=5) -> list[str]`
- [ ] 实现 `find_bridge_nodes_in_impact(impact_paths) -> list[str]`：
  - 在 impact_paths 里找只被单一路径依赖的中间节点（去掉这个节点后影响链断裂）
  - 简化版：出现在 ≥ 2 条不同路径上的节点即为桥接节点
- [ ] 确认测试通过
- [ ] commit：`feat: find_untested_hotspots and find_bridge_nodes_in_impact`

---

## Task 3：Review 问题生成

**文件：** `phases/surprise_analyzer.py`

- [ ] 写失败测试：
  - `test_generates_cross_module_question` — surprise edge → "是有意的吗？"
  - `test_generates_hotspot_question` — untested hotspot → "没有测试覆盖"
  - `test_max_questions_capped_at_7` — 信号很多时不超过 7 个问题
  - `test_no_questions_for_low_impact` — 影响范围小时返回空列表
- [ ] 确认测试失败
- [ ] 实现 `generate_review_questions(surprise_edges, hotspots, bridges, max_questions=7) -> list[str]`：
  - 优先级：surprise_score 高的先 → 无测试热点 → 桥接节点
  - 每类最多 3 个，总上限 7 个
  - 问题用中文，包含具体文件名和行为描述
- [ ] 确认测试通过
- [ ] `pytest tests/test_surprise_analyzer.py -v` 全绿
- [ ] commit：`feat: generate_review_questions from graph signals`

---

## Task 4：注入 LLM Prompt

**文件：** `phases/context_pack.py`、`phases/blast_radius.py`

- [ ] `context_pack.py`：`build_context_pack` 末尾调用 `generate_review_questions`，结果存入 `pack["review_questions"]`
- [ ] `blast_radius.py`：system prompt 末尾追加：
  ```
  以下是基于代码图谱自动发现的审查关注点，请在审查时优先回应这些问题：
  {review_questions_formatted}
  ```
- [ ] 写测试：`test_blast_radius_prompt_contains_review_questions`（mock LLM，检查 prompt）
- [ ] 确认测试通过
- [ ] `pytest -q` 全绿
- [ ] commit：`feat: inject review_questions into blast_radius LLM prompt`

---

## Task 5：终端渲染

**文件：** `terminal_renderer.py`、`reporter.py`

- [ ] `reporter.py`：`ReviewReport` 新增 `review_questions: list = field(default_factory=list)`
- [ ] `terminal_renderer.py`：在审查点矩阵前新增"自动发现的审查关注点"节（Rich Panel，黄色边框）
- [ ] 无问题时不渲染该节（静默）
- [ ] `--quiet` 模式下不渲染
- [ ] 写测试：`test_render_review_questions_shows_panel`
- [ ] 确认测试通过
- [ ] `pytest -q` 全绿
- [ ] commit：`feat: render review_questions panel before checkpoint matrix`

---

## Task 6：验证

```bash
pytest -q
luna --staged
```

验收标准：
- 跨目录调用关系被标记为可疑
- 无测试的高频函数出现在关注点列表
- LLM 审查回复中能看到针对这些问题的具体回答
- 低影响改动不产生关注点（不误报）

---

## Non-Goals（本阶段不做）

- 基于历史 review 数据学习哪些耦合是"有意的"
- 社区检测（Leiden，见后备计划），当前用目录近似
- 跨仓库 surprise scoring
