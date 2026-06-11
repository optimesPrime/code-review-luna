# 5 因子风险评分 — 量化替代 LLM 猜测 ✅ 已完成

**目标：** 在 LLM 审查之前，用 5 个可量化因子计算出每个改动符号的风险分数（0~1），转换为 high/medium/low 三档。让 Luna 的风险判断从"LLM 主观猜测"变为"数据驱动 + LLM 解释"。

**参考来源：** `/Users/wangyinlong/code-review-graph/code_review_graph/changes.py`（14KB，`compute_risk_score` 函数）

---

## 现在的问题

Luna 目前靠 LLM 判断 `risk: "high" | "medium" | "low"`，问题是：
- 不稳定：同一处改动，LLM 有时给 high，有时给 medium
- 不可解释：用户不知道"为什么是高风险"
- 消耗 token：LLM 在判断风险等级上花的 token，不如花在"解释为什么"上
- 无法量化进步：改善了测试覆盖率，风险分数应该下降，但 LLM 不感知

---

## 5 个风险因子

| 因子 | 计算方式 | 权重 | 含义 |
|------|----------|------|------|
| F1 影响深度 | BFS 最大深度 / max_depth | 0.25 | 改动波及多少层 |
| F2 调用者数量 | 直接调用者数 / 阈值(10) | 0.25 | 改动的 fan-in 有多大 |
| F3 跨模块调用 | 跨社区边数 / 总边数 | 0.20 | 改动是否跨越模块边界 |
| F4 测试覆盖 | 1 - (有测试文件 ? 0 : 1) | 0.15 | 有没有测试保护（反向：无测试=高风险） |
| F5 安全关键字 | 命中关键词数 / 5 | 0.15 | 是否涉及 auth/token/payment 等高敏感域 |

综合分数：`score = Σ(Fi × weight_i)`，截断到 `[0, 1]`

阈值：`score ≥ 0.65 → high`，`0.35 ≤ score < 0.65 → medium`，`< 0.35 → low`

---

## 安全关键词列表（F5）

```python
_HIGH_RISK_KEYWORDS = {
    # 认证/权限
    "auth", "login", "logout", "token", "jwt", "oauth", "session", "permission",
    "authorize", "authenticate", "credential", "password", "secret", "api_key",
    # 支付/交易
    "payment", "pay", "order", "trade", "transaction", "amount", "balance",
    "withdraw", "deposit", "settlement",
    # 数据操作
    "delete", "drop", "truncate", "migration", "schema",
    # 外部接口
    "webhook", "callback", "external", "third_party",
}
```

---

## LLM 角色的变化

**现在：** LLM 同时判断"是否高风险" + "为什么" + "建议如何修"

**之后：**
- 风险等级由评分系统计算（确定性、可解释）
- LLM prompt 中附上评分和命中因子："该改动评分 0.72（high），F1=0.8 影响 3 层，F5 命中关键词 auth/token"
- LLM 专注于"解释具体风险" + "给出修复建议"，不再猜等级

---

## 文件地图

| 操作 | 路径 | 职责 |
|------|------|------|
| Create | `phases/risk_scorer.py` | 5 因子计算、score→tier 转换、因子报告生成 |
| Modify | `phases/risk_propagation.py` | 在 `ImpactPath` 上附加 `risk_score` 和 `factor_report` |
| Modify | `phases/context_pack.py` | 把评分结果写入 context pack 给 LLM |
| Modify | `phases/blast_radius.py` | LLM prompt 中注入评分信息 |
| Modify | `reporter.py` | `BlastRadiusItem` 新增 `risk_score: float` 字段 |
| Modify | `terminal_renderer.py` | 审查点矩阵显示评分数值（可选） |
| Create | `tests/test_risk_scorer.py` | 测试各因子计算和综合评分 |

---

## 核心数据模型

```python
@dataclass
class RiskFactors:
    impact_depth: float       # F1: 0~1
    caller_count: float       # F2: 0~1
    cross_module: float       # F3: 0~1
    test_coverage: float      # F4: 0（有测试）或 1（无测试）
    security_keywords: float  # F5: 0~1

@dataclass
class RiskScore:
    score: float              # 0~1
    tier: str                 # "high" | "medium" | "low"
    factors: RiskFactors
    evidence: list[str]       # ["F1: 影响 3 层依赖", "F5: 命中 auth, token"]
```

---

## Task 1：5 因子计算

**文件：** `phases/risk_scorer.py`

- [ ] 写失败测试：
  - `test_score_deep_impact_is_high` — max_depth 命中 → F1 高
  - `test_score_no_test_increases_risk` — 无测试 → F4 = 1.0
  - `test_score_auth_keyword_increases_risk` — 符号名含 auth → F5 > 0
  - `test_score_well_tested_shallow_change_is_low` — 有测试、1 层影响 → low
  - `test_score_to_tier_thresholds` — 0.7 → high，0.5 → medium，0.2 → low
- [ ] 确认测试失败
- [ ] 实现 `compute_f1_impact_depth(impact_paths, max_depth) -> float`
- [ ] 实现 `compute_f2_caller_count(callers: list[str], threshold=10) -> float`
- [ ] 实现 `compute_f3_cross_module(impact_files: list[str], changed_file: str) -> float`
  - 简化版：影响文件中与改动文件不在同一目录的比例
- [ ] 实现 `compute_f4_test_coverage(changed_file: str, related_tests: list) -> float`
- [ ] 实现 `compute_f5_security_keywords(symbol_name: str, evidence: str) -> float`
- [ ] 实现 `compute_risk_score(symbol, impact_paths, callers, related_tests) -> RiskScore`
- [ ] 实现 `score_to_tier(score: float) -> str`（0.65 / 0.35 阈值）
- [ ] 确认测试通过
- [ ] `pytest tests/test_risk_scorer.py -v` 全绿
- [ ] commit：`feat: risk_scorer — 5-factor quantitative risk scoring`

---

## Task 2：接入 risk_propagation

**文件：** `phases/risk_propagation.py`

- [ ] `ImpactPath` dataclass 新增 `risk_score: float = 0.0` 和 `factor_evidence: list[str] = field(default_factory=list)`
- [ ] `propagate_risk` 在构建每条 `ImpactPath` 后调用 `compute_risk_score`，填入评分
- [ ] 用评分结果覆盖原来的 `risk`（high/medium/low），保持接口不变
- [ ] 写测试：`test_propagate_risk_attaches_score_to_impact_path`
- [ ] 确认测试通过
- [ ] `pytest -q` 全绿
- [ ] commit：`feat: risk_propagation attaches computed risk_score to ImpactPath`

---

## Task 3：注入 LLM Prompt

**文件：** `phases/context_pack.py`、`phases/blast_radius.py`

- [ ] `context_pack.py`：序列化 `RiskScore` 到 context pack JSON：
  ```json
  "risk_assessment": {
    "score": 0.72,
    "tier": "high",
    "evidence": ["F1: 影响 3 层依赖", "F5: 命中关键词 auth, token"]
  }
  ```
- [ ] `blast_radius.py` 的 LLM system prompt 新增：
  > "审查时请参考 risk_assessment 中的量化评分，重点解释 evidence 中列出的风险因子，不必重新判断风险等级。"
- [ ] 写测试：`test_context_pack_includes_risk_assessment`
- [ ] 确认测试通过
- [ ] `pytest -q` 全绿
- [ ] commit：`feat: inject risk_score into LLM prompt context`

---

## Task 4：终端展示

**文件：** `reporter.py`、`terminal_renderer.py`

- [ ] `BlastRadiusItem` 新增 `risk_score: float = 0.0`（默认 0 保持向后兼容）
- [ ] 审查点矩阵的风险说明列新增括号内显示分数：`⚠️ 中 (0.52)`
- [ ] 修复队列的 impact 列根据 `risk_score` 精确判断"阻塞/高价值/建议/延后"
- [ ] 写测试：`test_checkpoint_matrix_shows_risk_score`
- [ ] 确认测试通过
- [ ] `pytest -q` 全绿
- [ ] commit：`feat: display risk_score in checkpoint matrix and fix queue`

---

## Task 5：阈值调优入口

让用户可以自定义 high/medium 阈值：

- [ ] `config.py` 新增：
  ```yaml
  risk_scoring:
    enabled: true
    high_threshold: 0.65
    medium_threshold: 0.35
    weights:
      impact_depth: 0.25
      caller_count: 0.25
      cross_module: 0.20
      test_coverage: 0.15
      security_keywords: 0.15
  ```
- [ ] `risk_scorer.py` 从 `cfg.risk_scoring` 读取权重和阈值
- [ ] 写测试：`test_custom_thresholds_change_tier`
- [ ] 确认测试通过
- [ ] `pytest -q` 全绿
- [ ] commit：`feat: configurable risk scoring thresholds and weights`

---

## Task 6：验证

```bash
pytest -q
```

手动验证（在真实项目上对比）：

```bash
luna --staged
# 观察：blast radius items 是否带有 risk_score
# 观察：含 auth/token 关键字的改动是否评分更高
# 观察：有测试的改动评分是否低于无测试的类似改动
```

验收标准：
- 同一处改动多次运行，评分完全一致（确定性）
- auth/payment 相关改动评分 ≥ 0.6
- 有测试覆盖的改动比无测试同类改动评分低 0.1-0.15
- LLM 响应中不再出现"建议判断为高/中/低风险"的描述，改为"解释已评估的高风险因子"

---

## Non-Goals（本阶段不做）

- 机器学习模型替代规则权重（先用规则，积累数据后再考虑）
- 基于 git 历史的动态阈值调整
- 基于团队反馈的权重自适应更新（feedback loop 另立计划）
