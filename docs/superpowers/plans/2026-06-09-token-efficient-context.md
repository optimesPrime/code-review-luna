# Token 高效上下文 — 结构化摘要替代原始 Diff ✅ 已完成

**目标：** 把 Luna 传给 LLM 的内容从"原始 diff 文本 + 大块代码"换成"结构化分析摘要 + 精准代码片段"，预计节省 80-90% token，同时让 LLM 专注于解释和建议，而不是从原始代码里推断风险。

**参考来源：**
- `/Users/wangyinlong/code-review-graph/code_review_graph/context_savings.py`
- `/Users/wangyinlong/code-review-graph/code_review_graph/tools/review.py`（`_extract_relevant_lines`）
- `/Users/wangyinlong/code-review-graph/code_review_graph/prompts.py`
- `/Users/wangyinlong/code-review-graph/code_review_graph/hints.py`

**参考数据：** 原项目在 6 个真实仓库上实测 38~528 倍 token 缩减，保守估计 Luna 可达 **5~15 倍**缩减。

---

## 现在的问题

Luna 每次 review 把以下内容全部塞进 LLM prompt：

```python
# phases/blast_radius.py (当前)
user = f"## Git Diff\n\n```diff\n{diff}\n```\n\n## 上下文\n{context_pack_json}"
```

一个中型前端项目改了 3 个文件，diff 就可能有 200 行，再加上 context_pack 里的代码片段，轻松 5000-10000 tokens/次调用。Luna 有 3 个阶段调 LLM（blast_radius + code_quality + backend_review），一次完整 review 可能消耗 **15000-30000 tokens**。

---

## 核心思路

```
现在：
  diff 原文（200 行）→ LLM 自己理解风险

改造后：
  Luna 静态分析（免 token）→ 结构化摘要（50-100 tokens）→ LLM 解释和建议

结构化摘要 = {
  changed_symbols,    ← 哪些函数/组件变了
  risk_assessment,    ← 5因子评分（见 risk-scoring 计划书）
  impact_summary,     ← BFS 影响了哪些文件（只传文件名，不传内容）
  relevant_snippets,  ← 只截取变动函数的代码，不传整个文件
  test_gaps,          ← 缺少测试的地方
}
```

---

## 四个机制

### 机制 1：结构化 Context Pack（最大收益）

废弃"传整个 diff 文本"，改传 Luna 已经分析好的结构化数据。LLM 不需要重新阅读 diff，只需要基于结构化数据给出解释和建议。

**LLM 的工作从：**
> "读这 200 行 diff，判断有没有风险，如果有，是什么风险"

**变成：**
> "已知改动了 `handleSubmit`（Login.vue:74），风险评分 0.72（high），影响了 3 个文件，命中关键词 auth，无测试覆盖。请解释具体风险并给出修复建议。"

### 机制 2：精准代码片段（`_extract_relevant_lines`）

不传整个文件，只截取变动函数的 `[start_line-2, end_line+3]` 区间。一个 500 行的 Vue 文件，变动的函数只有 20 行，就只传这 20 行。

### 机制 3：detail_level 三档

| 档位 | 触发条件 | 传给 LLM 的内容 | 估计 token |
|------|----------|----------------|------------|
| minimal | `--quiet` | 符号名 + 风险分 + 影响文件名 | 100-300 |
| standard | 默认 | minimal + 精准代码片段 | 500-1500 |
| verbose | `--details` | standard + 完整 diff + 完整影响链路 | 无限制 |

### 机制 4：Token 节省估算展示

每次 review 完，展示"如果直接喂 diff，需要 X tokens；实际使用 Y tokens，节省 Z%"，让用户看到价值。

---

## 架构

```
phases/context_builder.py（新增）
  ├── build_minimal_context(symbols, risk, impact)   → dict  ~100 tokens
  ├── build_standard_context(symbols, risk, impact,
  │                          diff, project_root)     → dict  ~500-1500 tokens
  ├── extract_relevant_snippets(diff, project_root)  → dict  只截取变动函数体
  └── estimate_tokens(obj)                           → int   4 chars/token 估算

phases/blast_radius.py（修改）
  └── analyze() 改用 context_builder.build_standard_context

phases/code_quality.py（修改）
  └── analyze() 改用精准代码片段，不再传整个 diff

phases/context_savings.py（新增，迁移自 code-review-graph）
  ├── estimate_tokens(obj)
  ├── estimate_diff_tokens(diff)         ← baseline
  └── build_savings_summary(baseline, used)
```

---

## 文件地图

| 操作 | 路径 | 职责 |
|------|------|------|
| Create | `phases/context_builder.py` | 三档 context 构建 + `extract_relevant_snippets` |
| Create | `phases/context_savings.py` | token 估算（从 code-review-graph 移植，简化版） |
| Modify | `phases/blast_radius.py` | 用 `context_builder` 替换原始 diff 传递 |
| Modify | `phases/code_quality.py` | 用精准代码片段替换整个 diff |
| Modify | `phases/backend_review.py` | 同上 |
| Modify | `phases/context_pack.py` | 支持 detail_level，输出三档不同密度 |
| Modify | `luna.py` | `--details` flag 触发 verbose 模式 |
| Modify | `terminal_renderer.py` | 渲染 token 节省面板 |
| Modify | `reporter.py` | `ReviewReport` 新增 `token_savings` 字段 |
| Create | `tests/test_context_builder.py` | 测试三档输出和代码片段截取 |
| Create | `tests/test_context_savings.py` | 测试 token 估算 |

---

## Task 1：`extract_relevant_snippets` — 精准代码片段

**文件：** `phases/context_builder.py`

只截取变动函数的代码体，不传整个文件。

- [ ] 写失败测试：
  - `test_extract_snippets_returns_only_changed_function(tmp_path)` — 500 行文件，只截取 20 行变动函数
  - `test_extract_snippets_merges_overlapping_ranges(tmp_path)` — 两个紧邻函数合并成一个区间
  - `test_extract_snippets_caps_at_max_lines(tmp_path)` — 单个函数超过 150 行时只取前 150 行 + `...`
  - `test_extract_snippets_returns_empty_for_missing_file(tmp_path)` — 文件不存在时返回空
- [ ] 确认测试失败
- [ ] 实现 `extract_relevant_snippets(changed_symbols: list[ChangedSymbol], project_root: str, context_lines: int = 3) -> dict[str, str]`：
  - 按 `symbol.file` 分组
  - 每组取 `[start_line - context_lines, end_line + context_lines]` 区间
  - 相邻区间（距离 < 5 行）合并
  - 单文件总行数超过 `max_lines=150` 时截断加 `\n... (truncated)`
  - 返回 `{rel_path: snippet_text}`
- [ ] 确认测试通过
- [ ] `pytest tests/test_context_builder.py -v` 通过
- [ ] commit：`feat: extract_relevant_snippets — only changed function bodies`

---

## Task 2：三档 Context Builder

**文件：** `phases/context_builder.py`

- [ ] 写失败测试：
  - `test_minimal_context_has_no_code` — minimal 不含任何源码
  - `test_standard_context_has_snippets` — standard 含 `relevant_snippets`
  - `test_minimal_context_under_500_chars` — minimal 输出序列化后 < 500 字符（约 125 tokens）
  - `test_detail_level_verbose_includes_full_diff` — verbose 含完整 diff
- [ ] 确认测试失败
- [ ] 实现 `build_minimal_context(symbols, risk_items, impact_paths) -> dict`：

```python
{
  "changed_symbols": [{"file": s.file, "symbol": s.symbol, "type": s.symbol_type, "line": s.start_line}
                       for s in symbols],
  "risk_summary": {
    "high": len([i for i in risk_items if i.risk == "high"]),
    "medium": len([i for i in risk_items if i.risk == "medium"]),
    "top_risks": [{"symbol": i.symbol, "reason": i.reason[:80]}
                  for i in risk_items if i.risk == "high"][:3],
  },
  "impact_files": list({p.path[-1] for p in impact_paths if p.path})[:10],
  "test_gaps": [i.symbol for i in risk_items if i.needs_human_review][:5],
}
```

- [ ] 实现 `build_standard_context(symbols, risk_items, impact_paths, diff, project_root) -> dict`：
  - minimal 内容 + `relevant_snippets` + `risk_factors`（5 因子详情，若已计算）
  - `relevant_snippets` 来自 `extract_relevant_snippets`
- [ ] 实现 `build_verbose_context(...) -> dict`：
  - standard 内容 + 完整 diff + 完整影响链路
- [ ] 确认测试通过
- [ ] `pytest tests/test_context_builder.py -v` 全绿
- [ ] commit：`feat: context_builder — three detail_level tiers (minimal/standard/verbose)`

---

## Task 3：Token 估算模块

**文件：** `phases/context_savings.py`（从 code-review-graph 简化移植）

- [ ] 写失败测试：
  - `test_estimate_tokens_string` — `"hello world"` → 3（11 chars / 4 ≈ 3）
  - `test_estimate_tokens_dict` — JSON 序列化后估算
  - `test_estimate_diff_tokens` — 通过 len(diff) 估算
  - `test_build_savings_summary_calculates_percent`
- [ ] 确认测试失败
- [ ] 实现 `estimate_tokens(obj: Any) -> int`（4 chars/token）
- [ ] 实现 `estimate_diff_tokens(diff: str) -> int`（baseline：如果传整个 diff）
- [ ] 实现 `build_savings_summary(baseline_tokens: int, used_tokens: int) -> dict`：
  ```python
  {"baseline": baseline, "used": used,
   "saved": baseline - used, "saved_percent": int((1 - used/baseline) * 100)}
  ```
- [ ] 确认测试通过
- [ ] `pytest tests/test_context_savings.py -v` 通过
- [ ] commit：`feat: context_savings — token estimation and savings summary`

---

## Task 4：接入 blast_radius 和 code_quality

**文件：** `phases/blast_radius.py`、`phases/code_quality.py`、`phases/backend_review.py`

- [ ] 修改 `blast_radius.analyze()`：
  - 在 LLM 调用前，用 `context_builder.build_standard_context` 构建 prompt 的 user 部分
  - 不再直接传 `diff` 原文（除非 `detail_level="verbose"`）
  - 调用前记录 `baseline = estimate_diff_tokens(diff)`
  - 调用后记录 `used = estimate_tokens(user_prompt)`
  - 返回时附带 `savings = build_savings_summary(baseline, used)`
- [ ] 修改 `code_quality.analyze()`：同样改用 `extract_relevant_snippets` + 结构化上下文
- [ ] 修改 `backend_review.analyze_backend()`：同上，只传变动后端符号的代码片段
- [ ] 写测试：`test_blast_radius_prompt_does_not_contain_full_diff`（mock LLM，检查 user prompt 长度）
- [ ] 确认测试通过
- [ ] `pytest -q` 全绿
- [ ] commit：`feat: blast_radius and code_quality use structured context instead of raw diff`

---

## Task 5：`--details` flag + detail_level 传递

**文件：** `luna.py`

- [ ] 给 `cli()` 新增 `@click.option("--details", "detail_level", flag_value="verbose", default="standard")`
- [ ] `detail_level` 通过 `cfg` 或函数参数传入 `blast_radius.analyze` 等
- [ ] `--quiet` 时自动降为 `minimal`
- [ ] 写测试：`test_quiet_flag_uses_minimal_context`
- [ ] 确认测试通过
- [ ] `pytest -q` 全绿
- [ ] commit：`feat: --details flag for verbose context; --quiet forces minimal`

---

## Task 6：Token 节省面板

**文件：** `terminal_renderer.py`、`reporter.py`

每次 review 完，在报告底部展示：

```
┌─────────────────── Token 使用情况 ───────────────────┐
│ 如果直接传 diff：     8,421 tokens                   │
│ 实际使用：              763 tokens                   │
│ 节省：               7,658 tokens（91%）             │
└──────────────────────────────────────────────────────┘
```

- [ ] `ReviewReport` 新增 `token_savings: dict = field(default_factory=dict)`
- [ ] `luna.py` 在全部分析完成后汇总三个阶段的 savings 数字
- [ ] `terminal_renderer.py` 新增 `render_token_savings(savings)` — Rich Panel
- [ ] `--quiet` 模式下不渲染该 panel
- [ ] 写测试：`test_render_token_savings_shows_percent`
- [ ] 确认测试通过
- [ ] `pytest -q` 全绿
- [ ] commit：`feat: token savings panel in terminal output`

---

## Task 7：验证

```bash
pytest -q
```

手动验证（在真实项目上对比）：

```bash
# 标准模式
luna --staged
# 注意底部 Token 使用情况面板

# verbose 模式（传完整 diff，用于对比）
luna --staged --details

# 安静模式（最小上下文）
luna --staged --quiet
```

验收标准：
- standard 模式下 LLM prompt 不含完整 diff 文本
- token 节省率 ≥ 60%（对比 verbose 的 baseline）
- `--details` 模式输出结果与之前基本一致（regression 检查）
- `--quiet` 模式 prompt < 500 tokens
- 审查质量不下降（LLM 仍然能给出具体的文件:行号引用）

---

## 与其他计划书的关系

| 计划书 | 关系 |
|--------|------|
| `risk-scoring.md` | Task 2 的 `build_minimal_context` 依赖 `RiskScore` 对象，两者同步开发效益最大 |
| `sqlite-graph-store.md` | SQLite 落地后 `impact_files` 来自 `GraphDB.bfs_impact`，质量更高 |
| `hybrid-search-rrf.md` | hybrid search 的结果作为 `impact_files` 替代纯 BFS，进一步提升 context 质量 |
| `luna-fix.md` | `generate_fix` 也受益于精准代码片段（只传变动函数体给 LLM，不传整个文件） |

---

## Non-Goals（本阶段不做）

- Prompt caching（Anthropic 的 prefix cache，独立优化项）
- 向量嵌入 diff（需要 embedding provider 配置）
- 按历史 review 质量动态调整 detail_level
- 多轮对话（当前是单轮审查）
