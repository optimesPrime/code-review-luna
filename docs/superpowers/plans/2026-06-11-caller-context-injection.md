# ✅ 已完成 · Caller Context Injection — 让 Luna 知道调用方怎么用

**目标：** 给每个改动符号自动提取「调用点 + 周围代码片段」作为证据喂给 LLM，让 LLM 在判断"这个改动影响调用方吗"时有真凭实据，不再因信息不足而保守标 high。

**参考代码：**
- `phases/blast_radius.py:70` — `find_usages_in_project`（已有的 grep 逻辑可复用）
- `phases/context_pack.py` — `ContextPack` 已有 `review_focus / review_questions / related_tests` 等字段，本计划新增 `caller_contexts`
- `phases/context_builder.py` — `extract_relevant_snippets`（已有的行号截取逻辑可参考）

**参考格式：** `docs/superpowers/plans/2026-06-09-token-efficient-context.md`

---

## 现在的问题

Luna 喂给 LLM 的上下文里能看到：
- 改了什么（changed_symbols）
- 谁依赖了改动的文件（impact_paths）
- 改动文件里的相关代码片段（relevant_snippets）

但**看不到**：调用方到底**怎么用**这个符号？

### 真实案例

今天 Luna 自审 SQLite 改动时，反复把"`graph.nodes` 不完整"标 high 风险。但实际情况是：

```
luna.py:280  → graph = build_graph(".")
luna.py:285  → propagate_risk(symbols, graph)    # 内部只调 graph.find_usages()
luna.py:299  → for _e in graph.edges:           # 用了 graph.edges
                                                  # 从未访问 graph.nodes
```

如果 LLM 能看到调用方代码，它会立刻发现"`graph.nodes` 不被任何下游使用"，把这条风险降到 low。**这一类信息差导致的误判，是当前 Luna 精准度的最大瓶颈。**

---

## 核心思路

```
现在：
  changed_symbol = build_graph     ← LLM 只看到这个
  ↓
  LLM 推断：build_graph 改了，调用方可能受影响 → 标 high

加了调用方上下文之后：
  changed_symbol = build_graph
  caller_contexts = [
    {
      file: "luna.py", line: 280,
      snippet: "graph = build_graph('.')\n... uses graph.edges, graph.find_usages() ..."
    }
  ]
  ↓
  LLM 推断：调用方只用 graph.edges，nodes 部分改动不影响 → 标 low
```

---

## 受益场景（不止 SQLite 这一例）

| 场景 | 现在 | 有 caller_contexts |
|------|------|-------------------|
| 函数返回值结构变了 | 标 high（不知调用方用不用那字段） | 看调用方是否访问该字段 |
| 函数参数类型改了 | 标 high | 看调用方实际传了什么 |
| 接口删了一个方法 | 标 high | 看是否有调用方真的调了 |
| DB 删了一列 | 标 high | 看 ORM/SQL 是否读写了该列 |
| 工具函数签名变了 | 标 high | 看所有调用点是否同步改了 |

本质：让 LLM 从「假设调用方依赖一切」升级为「看见调用方实际依赖什么」。

---

## 架构

```
phases/caller_context.py（新增）
  ├── @dataclass CallerSnippet(file, line, snippet, language)
  ├── @dataclass SymbolCallers(symbol, callers: list[CallerSnippet])
  ├── grep_call_sites(symbol, project_root, ignore_dirs) → list[(file, line)]
  ├── extract_call_snippet(file, line, context_lines=5) → str
  └── build_caller_contexts(symbols, project_root, ignore_dirs,
                            max_callers_per_symbol=5,
                            max_snippet_lines=12) → list[SymbolCallers]

phases/context_pack.py（修改）
  └── ContextPack 新增 caller_contexts: list[SymbolCallers]

phases/blast_radius.py（修改）
  └── analyze() 把 caller_contexts 注入 prompt（standard/verbose 模式）

phases/code_quality.py（修改）
  └── analyze() 同上

phases/backend_review.py（修改）
  └── analyze_backend() 同上
```

---

## 关键设计决策

### 1. 用 grep，不用 AST

复用 `find_usages_in_project` 已有的 grep 流程，不引入 tree-sitter 跨语言调用图。理由：
- 精准度不需要那么细 —— LLM 只要看到「该符号被谁调用、附近代码长啥样」就够推理
- 实现简单、跨语言天然支持（.py/.ts/.java/.go 全覆盖）
- 接受 false positive（变量名相同但不是真调用）—— LLM 看到 snippet 能自己识别

### 2. 三级裁剪，避免 token 爆炸

- 每个符号最多取 `max_callers_per_symbol=5` 个调用点（按"距离改动文件越近优先级越高"排序）
- 每个调用点最多 `max_snippet_lines=12` 行（行号 ±5 行）
- 总调用点数上限 `total_callers_cap=20`（防止改动符号过多时 prompt 失控）

### 3. 与 detail_level 协同

| detail_level | caller_contexts 行为 |
|--------------|---------------------|
| `minimal`（`--quiet`） | 只保留调用点数量，不传 snippet（≈ "build_graph: 3 callers"） |
| `standard`（默认） | 传完整 caller_contexts（snippet 已裁剪） |
| `verbose`（`--details`） | 传完整 caller_contexts + 完整 diff |

### 4. 排除自身改动文件

`grep` 出的调用点如果落在「改动文件本身」，跳过 —— 改动文件的代码已经通过 `relevant_snippets` 传给 LLM 了。caller_contexts 只关注「外部调用方」。

### 5. 排除注释行（启发式）

调用点行内容如果以 `#`、`//`、`/*`、`*` 开头，跳过。简单但够用。

---

## 文件地图

| 操作 | 路径 | 职责 |
|------|------|------|
| Create | `phases/caller_context.py` | grep 调用点 + 截取片段 + 汇总 |
| Modify | `phases/context_pack.py` | `ContextPack` 新增 `caller_contexts` 字段 + 序列化 |
| Modify | `phases/blast_radius.py` | prompt 注入 caller_contexts |
| Modify | `phases/code_quality.py` | 同上 |
| Modify | `phases/backend_review.py` | 同上 |
| Modify | `luna.py` | 在 `build_context_pack()` 调用后填充 caller_contexts |
| Modify | `config.py` | 新增 `CallerContextConfig`（开关 + 阈值） |
| Create | `tests/test_caller_context.py` | 测试 grep / 截取 / 排除规则 |

---

## 核心数据模型

```python
@dataclass
class CallerSnippet:
    file: str           # 相对路径，例如 "luna.py"
    line: int           # 调用点行号（1-indexed）
    snippet: str        # 行号 ±5 行的代码
    language: str       # "python" | "typescript" | "vue" | ...

@dataclass
class SymbolCallers:
    symbol: str                       # "build_graph"
    callers: list[CallerSnippet]      # 调用点列表（已裁剪）
    total_count: int                  # grep 找到的总数（可能 > len(callers)）
```

注入到 `ContextPack.to_dict()` 后的 JSON 形态：

```json
"caller_contexts": [
  {
    "symbol": "build_graph",
    "total_callers": 3,
    "callers_shown": 3,
    "callers": [
      {
        "file": "luna.py",
        "line": 280,
        "language": "python",
        "snippet": "...\n            graph = build_graph(\".\")\n            _finish(\"frontend_graph\")\n            symbols = extract_changed_symbols_from_diff(diff, project_root=\".\")\n            impact_paths = propagate_risk(symbols, graph)\n..."
      }
    ]
  }
]
```

---

## Task 1：`grep_call_sites` + `extract_call_snippet`

**文件：** `phases/caller_context.py`

- [ ] 写失败测试：
  - `test_grep_finds_caller_in_python_file(tmp_path)` — Python 调用点能被找到
  - `test_grep_finds_caller_in_typescript_file(tmp_path)` — TS/JS 调用点能被找到
  - `test_grep_excludes_self_file(tmp_path)` — 改动文件本身的调用不会被返回
  - `test_grep_excludes_comment_lines(tmp_path)` — `# build_graph(...)` 这种注释行被排除
  - `test_grep_returns_empty_for_unused_symbol(tmp_path)` — 没人调用时返回 `[]`
  - `test_grep_respects_ignore_dirs(tmp_path)` — `node_modules/` 等被跳过
- [ ] 确认测试失败
- [ ] 实现 `grep_call_sites(symbol: str, project_root: str, ignore_dirs: list[str], self_file: str | None = None) -> list[tuple[str, int]]`：
  - 调用 `subprocess.run(["grep", "-rn", ...])`，复用 `find_usages_in_project` 的 include/exclude 参数
  - 解析输出格式 `path:line:content`，过滤注释行、`self_file`
  - 排序：先按 `os.path.dirname(file)` 与 `self_file` 的目录共同前缀长度倒序，再按 `(file, line)` 升序
  - 返回 `[(rel_path, line), ...]`
- [ ] 实现 `extract_call_snippet(file_path: str, line: int, context_lines: int = 5) -> str`：
  - 读文件，取 `[line - context_lines, line + context_lines]` 区间
  - 行数超过 `max_lines=12` 时取前 12 行 + `... (truncated)`
  - 文件不存在 / OSError → 返回空串
- [ ] 确认测试通过
- [ ] `pytest tests/test_caller_context.py -v` 全绿
- [ ] **暂停，等用户确认是否提交**

---

## Task 2：`build_caller_contexts` 汇总

**文件：** `phases/caller_context.py`

- [ ] 写失败测试：
  - `test_build_caller_contexts_per_symbol(tmp_path)` — 每个 symbol 一条记录
  - `test_caller_contexts_caps_per_symbol(tmp_path)` — 单个 symbol 超过 max_callers_per_symbol 时截断
  - `test_caller_contexts_total_cap_enforced(tmp_path)` — 全部 symbol 总调用点不超过 total_cap
  - `test_caller_contexts_records_total_count(tmp_path)` — 即使裁剪了，total_callers 字段记录真实总数
  - `test_caller_contexts_skips_self_file(tmp_path)` — symbol 自己的文件不出现在 callers 列表
- [ ] 确认测试失败
- [ ] 实现 `build_caller_contexts(symbols: list[ChangedSymbol], project_root: str, ignore_dirs: list[str], max_callers_per_symbol: int = 5, max_snippet_lines: int = 12, total_callers_cap: int = 20) -> list[SymbolCallers]`：
  - 对每个 symbol：grep + 排序 + 取前 `max_callers_per_symbol` 个
  - 每个调用点：调用 `extract_call_snippet`
  - 全局计数超过 `total_callers_cap` 时停止追加（但仍记录 total_count）
  - 跳过 symbol 自己所在文件
  - language 推断：按后缀映射（.py → python，.ts/.tsx → typescript，.vue → vue，.java → java，.go → go，其他 → "unknown"）
- [ ] 确认测试通过
- [ ] `pytest tests/test_caller_context.py -v` 全绿
- [ ] **暂停，等用户确认是否提交**

---

## Task 3：接入 `ContextPack`

**文件：** `phases/context_pack.py`

- [ ] 写失败测试：
  - `test_context_pack_serializes_caller_contexts` — `to_dict()` 包含 `caller_contexts` 数组
  - `test_context_pack_empty_caller_contexts_serializes_empty_list` — 没有调用方时序列化为 `[]`
- [ ] 确认测试失败
- [ ] `ContextPack` 新增字段：

```python
caller_contexts: list = field(default_factory=list)  # list[SymbolCallers]
```

- [ ] `to_dict()` 序列化：

```python
"caller_contexts": [
    {
        "symbol": sc.symbol,
        "total_callers": sc.total_count,
        "callers_shown": len(sc.callers),
        "callers": [
            {"file": c.file, "line": c.line, "language": c.language, "snippet": c.snippet}
            for c in sc.callers
        ],
    }
    for sc in self.caller_contexts
],
```

- [ ] 确认测试通过
- [ ] `pytest -q` 全绿
- [ ] **暂停，等用户确认是否提交**

---

## Task 4：注入 LLM Prompt

**文件：** `phases/blast_radius.py`、`phases/code_quality.py`、`phases/backend_review.py`

- [ ] 写失败测试：
  - `test_blast_radius_prompt_contains_caller_contexts` — LLM user prompt 里能看到 caller_contexts JSON
  - `test_minimal_mode_omits_caller_snippets` — `detail_level="minimal"` 时只传 total_callers 数字，不传 snippet
  - `test_no_caller_contexts_when_symbols_empty` — 没改动符号时 caller_contexts 为空
- [ ] 确认测试失败
- [ ] `blast_radius.py` 的 `analyze()`：
  - context_pack 已有 caller_contexts，序列化 JSON 时自带（Task 3 已做）
  - 给 `_SYSTEM_PROMPT` 末尾加一段说明：

```
caller_contexts 字段包含「改动符号被外部调用的真实代码片段」。
请优先依据这些 snippet 判断调用方是否真实依赖被改动的部分：
  - 如果 snippet 显示调用方未访问被改字段/方法，应降低相关风险等级；
  - 如果 snippet 显示调用方密集依赖，应保持或提高风险等级；
  - 不要在缺乏 snippet 证据时默认假设调用方依赖一切。
```

- [ ] `code_quality.py`：同样在 system prompt 末尾加上述说明（即便 code_quality 不直接用 context_pack，也告诉 LLM 关注调用方）
- [ ] `backend_review.py`：同上
- [ ] 确认测试通过
- [ ] `pytest -q` 全绿
- [ ] **暂停，等用户确认是否提交**

---

## Task 5：接入主流程 + 配置化

**文件：** `luna.py`、`config.py`

- [ ] `config.py` 新增：

```python
@dataclass
class CallerContextConfig:
    enabled: bool = True
    max_callers_per_symbol: int = 5
    max_snippet_lines: int = 12
    total_callers_cap: int = 20
```

加入 `Config` 主类并从 YAML 读取。

- [ ] `luna.py` 在 `build_context_pack()` 之后填充 caller_contexts：

```python
if cfg.caller_context.enabled:
    from phases.caller_context import build_caller_contexts
    context_pack.caller_contexts = build_caller_contexts(
        symbols,
        project_root=".",
        ignore_dirs=cfg.privacy.ignore,
        max_callers_per_symbol=cfg.caller_context.max_callers_per_symbol,
        max_snippet_lines=cfg.caller_context.max_snippet_lines,
        total_callers_cap=cfg.caller_context.total_callers_cap,
    )
```

- [ ] 给进度阶段加入「调用方上下文构建」阶段：
  - `_phase_list += [("caller_ctx", "构建调用方上下文")]`（在 frontend_graph 之后，blast 之前）
- [ ] 写测试：`test_caller_context_config_loaded_from_yaml`
- [ ] 确认测试通过
- [ ] `pytest -q` 全绿
- [ ] **暂停，等用户确认是否提交**

---

## Task 6：终端渲染（可选展示）

**文件：** `terminal_renderer.py`、`reporter.py`

让用户在终端能看到 Luna 收集了哪些调用方上下文（增强透明度）。

- [ ] `reporter.py` `ReviewReport` 新增 `caller_contexts: list = field(default_factory=list)`，从 context_pack 拷贝过来
- [ ] `terminal_renderer.py` 在审查点矩阵之前新增"调用方上下文"折叠区：

```
🔗  调用方上下文（已采集 3 个符号 / 8 个调用点）
  build_graph (3 callers)
    luna.py:280, luna.py:299, tests/test_context_graph.py:42
  propagate_risk (2 callers)
    luna.py:285, tests/test_risk_propagation.py:11
```

只显示文件:行号清单，不展开 snippet（snippet 只给 LLM 看）。

- [ ] `--quiet` 模式不渲染此区
- [ ] 写测试：`test_render_caller_contexts_shows_symbol_count`
- [ ] 确认测试通过
- [ ] `pytest -q` 全绿
- [ ] **暂停，等用户确认是否提交**

---

## Task 7：验证

```bash
pytest -q
```

手动验证：

```bash
# 在 luna 仓库自审，对比改动前后
luna --staged

# 检查 reports/latest.json 里 context_pack 包含 caller_contexts
cat .luna-reports/latest.json | jq '.fix_candidates[0]'

# 关掉这个功能跑一次，对比 LLM 输出风险等级
# 临时把 ~/.luna/config.yaml 里 caller_context.enabled 改为 false
luna --staged
```

验收标准：
- 含 caller_contexts 时 LLM 风险标记更精准（high 数量下降，需配真实项目验证）
- caller_contexts 总 token 增加 ≤ 1500（受 cap 限制）
- 关掉 enabled 后行为完全等价于之前版本（回归无感）
- 改动符号被自己测试文件覆盖时，callers 里出现 `tests/*` 路径

---

## 与已完成计划的关系

| 计划 | 关系 |
|------|------|
| `token-efficient-context.md`（已完成） | caller_contexts 走 standard mode（默认传 snippet）；minimal 模式只传计数 |
| `surprise-scoring-review-questions.md`（已完成） | review_questions 和 caller_contexts 并列，前者是「Luna 提出的问题」，后者是「让 LLM 自己看证据」 |
| `risk-scoring.md`（已完成） | 5 因子打分提供量化基线，caller_contexts 提供质性证据，互补 |

---

## Non-Goals（本阶段不做）

- AST 级别的精确调用图（jedi / pyright / ts-morph）— grep + LLM 推理已够用
- 跨仓库的调用方追踪（多 repo monorepo）
- 调用点动态运行时分析（trace-based）
- 嵌入向量相似度搜索调用点（依赖向量库）
- 给 caller snippet 二次 LLM 摘要（先看裸截取效果，不够再考虑）

---

## 风险与缓解

| 风险 | 缓解 |
|------|------|
| grep false positive（变量名碰撞） | LLM 看到 snippet 能识别；caps 限制 token 总量 |
| 大仓库 grep 慢 | `subprocess.run(..., timeout=10)`，超时跳过该符号 |
| caller snippet 让 prompt 爆炸 | 三层裁剪（per-symbol / per-snippet / global），可配置 |
| 精准度反而下降（LLM 被 false positive 误导） | Task 7 强制对比 enabled/disabled 两次运行的 LLM 输出，验收关卡 |
| 跨语言 grep include 缺失 | grep 命令显式列出 `.py/.ts/.tsx/.vue/.java/.go/.cs/.rb/.php`，覆盖所有 Luna 支持的语言 |
