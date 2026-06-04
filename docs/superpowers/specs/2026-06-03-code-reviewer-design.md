# Code Reviewer 本地 AI 代码审查工具 · 设计文档

**日期**：2026-06-03  
**状态**：已批准  
**作者**：布哥哥 + Claude

---

## 一、背景与目标

搭建一套本地化 AI 代码审查工作流，支持个人先行使用，成熟后推广至团队。

**核心流程：**
```
开发者写完代码 → cr run → AI 初审（爆炸范围 + 代码质量）→ 人工复审 → 手动推送
```

**设计原则：**
- 不执行写入型 git 操作：不 `push`、不 `commit`、不 `checkout`、不 `reset`，只允许 `git diff`、`git status` 等只读命令
- 默认只输出审查意见和建议 patch；只有显式开启应用模式并逐条确认后，才允许写入文件
- 先覆盖前端（Vue/TS），架构支持后续扩展至后端

**本地化定义：**
本项目的“本地化”指 CLI、配置、报告和交互确认流程都在开发者本机运行；模型调用默认仍通过 Anthropic/OpenAI API。
工具必须避免把无关敏感文件发送给模型，并支持忽略规则、diff 大小限制和必要的内容脱敏。

---

## 二、整体架构

### 目录结构

```
code-reviewer/
├── cr.py                    # CLI 入口，注册为全局命令 `cr`
├── config.yaml              # API、模型、项目类型配置
├── phases/
│   ├── blast_radius.py      # 阶段1：爆炸范围分析
│   ├── code_quality.py      # 阶段2：代码质量审查
│   └── skill_runner.py      # Skill 加载与 prompt 注入
├── skills/                  # 自定义 skill 文件目录
│   └── vue-patterns.md      # 示例：Vue 规范 skill
├── reporter.py              # Markdown 报告生成
├── confirmer.py             # 统一交互确认模块
├── requirements.txt
└── docs/
    └── superpowers/specs/   # 设计文档存放处
```

### 报告存放

```
<项目目录>/.cr-reports/
└── 2026-06-03_143022_report.md   # 带时间戳，建议加入 .gitignore
```

### 数据流

```
git diff 输出
    ↓
[可选] 加载 skill 文件 → 注入阶段1/阶段2 system prompt
    ↓
[可选] 导入测试用例文件 → 提取测试描述，与改动做关联映射
    ↓
阶段1：爆炸范围分析 → 逐条交互确认
    ↓
阶段2：代码质量审查 → 逐条交互确认
    ↓
reporter 汇总 → Markdown 报告（终端摘要 + 文件保存）
```

---

## 三、命令行接口

```bash
cr run                          # 审查当前 git 未提交改动
cr run --staged                 # 只审查已 git add 的内容
cr run --tests ./tests/         # 导入测试目录做关联映射
cr run --phase blast            # 只跑爆炸范围阶段
cr run --phase quality          # 只跑代码质量阶段
cr run --output ./my-report.md  # 自定义报告输出路径
cr run --since main             # 审查当前分支相对 main 的改动
cr run --format json            # 输出 JSON，便于后续接 HTML 报告或 CI
cr run --apply                  # 开启可写入模式；仍需每条建议二次确认
```

---

## 四、各阶段设计

### 前置步骤：测试用例导入（可选）

通过 `--tests` 参数指定测试文件或目录，工具解析测试描述（`it()`、`describe()` 等），
在报告中标注哪些测试用例覆盖了本次改动范围，供复审参考。

> 当前阶段只做关联映射，不执行测试。后续可扩展为真正运行。

---

### 阶段1：爆炸范围分析（Blast Radius）—— 最高优先级

**核心概念：** 每个被改动的函数/组件/接口视为"爆炸点"，向外追踪所有依赖链路。

**v1 追踪范围：**
- 解析 Vue/TS 文件的 `import` / `export` 关系
- 识别函数、组件、composable、store、router 配置的直接调用或引用
- 支持常见路径别名（如 `@/`、`~/`），从项目配置或约定中解析
- 对 template 事件、动态 import、字符串路由名等难以静态确认的链路标记为低置信度

**风险与置信度：**
- 风险等级表示潜在影响程度：`high / medium / low`
- 置信度表示工具对依赖链路判断的把握：`high / medium / low`
- 高风险但低置信度的项仍要进入报告，但必须标注“需人工确认”

**输出示例：**
```
爆炸点：useAuth.js → refreshToken()
  └─ 被调用：3 处
      ├─ router/index.js:45     [风险: high] 路由守卫依赖此函数
      ├─ store/user.js:88       [风险: medium] 登录状态依赖
      └─ components/Header.vue  [风险: low] 仅读取状态

> 发现 1 处高风险影响，是否让 AI 给出修复建议？[y/N]
```

**交互规则：** 每处影响逐条询问，用户选 `y` 才输出详细建议；只有在 `--apply` 模式下，才继续询问是否写入文件。

---

### 阶段2：代码质量审查

在爆炸范围确认后，审查本次改动代码本身：

- 是否有冗余逻辑（重复代码、死代码）
- 是否有多余判断（永真/永假条件）
- 流程是否走通（关键路径的异常处理是否完整）

**输出示例：**
```
[代码质量] Login.vue:handleSubmit — 存在重复的 token 清理逻辑
（第 32 行与第 67 行重复）

> 是否让 AI 给出简化建议？[y/N]
> 生成 patch 供人工复制/应用？[y/N]
```

---

### Skill 扩展钩子

在 `config.yaml` 中声明要挂载的 skill：

```yaml
skills:
  - name: vue-patterns
    path: ./skills/vue-patterns.md
  - name: superpowers-security
    path: ./skills/security.md
```

每个 skill 文件是一段 prompt 片段，工具在审查开始前加载，并在调用 API 时自动注入到对应阶段的 system prompt。
团队可以把自己的审查规范、业务约定写成 skill 文件，像 superpowers 体系一样挂载共享。

如果 skill 文件不存在、读取失败或内容为空，本次审查不中断；报告中记录该 skill 未生效的原因。

---

## 五、配置文件（config.yaml）

```yaml
api:
  provider: anthropic              # 或 openai
  model: claude-sonnet-4-6
  api_key_env: ANTHROPIC_API_KEY   # 从环境变量读取，不写死

review:
  language: zh                     # 报告语言
  project_type: frontend           # frontend / backend / fullstack
  confirm_before_fix: true         # 硬性开启，禁止私自修改
  max_diff_chars: 120000           # 超过限制时提示用户缩小范围
  apply_enabled: false             # 默认不写入代码，需 CLI 显式开启

skills:
  - name: vue-patterns
    path: ./skills/vue-patterns.md

reports:
  output_dir: ./.cr-reports

privacy:
  ignore:
    - .env
    - .env.*
    - node_modules/
    - dist/
  redact_patterns:
    - "Bearer\\s+[A-Za-z0-9._-]+"
    - "AKIA[0-9A-Z]{16}"
```

---

## 六、交互确认机制

`confirm_before_fix: true` 在配置层强制开启，`confirmer.py` 在代码层做硬校验，确保无法绕过。
默认运行时只能生成建议和 patch，不写入源码。只有用户执行 `cr run --apply` 后，才进入可写入模式；可写入模式下每条建议仍必须二次确认。

**`--apply` 模式交互流程：**
```
[爆炸范围·high] router/index.js:45 — 路由守卫依赖 refreshToken，
改动后可能导致未登录用户访问受保护页面。

建议修复：在路由守卫中增加 token 有效性二次校验。

> 查看详细建议？[y/N] y
  --- 建议内容 ---
  在 router/index.js 第 45 行的 beforeEach 中追加：
  if (!isTokenValid(store.state.token)) next('/login')
  ----------------
> 应用此修改？[y/N] y  ← 写入文件
```

所有已应用/已跳过的项目均记录在最终报告里，供人工复审。

如果未开启 `--apply`，最后一步改为：
```
> 生成 patch 供人工复制/应用？[y/N]
```

---

## 七、Markdown 报告结构

```markdown
# 代码审查报告 · 2026-06-03 14:30

## 一、改动概述
## 二、改动文件清单
## 三、爆炸范围分析
   - 高风险影响列表
   - 中/低风险影响列表
   - 每条记录包含：文件位置、影响链路、风险等级、置信度、证据、建议操作、用户决策
   - AI 已生成 patch / 已应用修复 / 已跳过项
## 四、代码质量问题
   - 问题列表
   - 每条记录包含：文件位置、问题描述、判断依据、风险等级、置信度、建议操作、用户决策
   - AI 已生成 patch / 已应用修复 / 已跳过项
## 五、关联测试用例（如有导入）
## 六、审查结论（人工复审填写）
```

---

## 八、技术选型

| 模块 | 技术 |
|------|------|
| 语言 | Python 3.11+ |
| CLI 框架 | `click` |
| API 调用 | `anthropic` SDK（默认），可切换 OpenAI |
| 配置管理 | `config.yaml` + 环境变量覆盖 |
| 报告渲染 | `jinja2` 模板 → Markdown |
| 安装方式 | `pipx install .` 注册为全局 `cr` 命令 |

---

## 九、失败路径与降级策略

| 场景 | 处理方式 |
|------|----------|
| 当前目录不是 git 仓库 | 提示错误并退出，不调用模型 |
| 没有可审查 diff | 输出“无改动”，不生成空报告 |
| diff 超过 `max_diff_chars` | 提示用户使用 `--staged`、`--since` 或指定更小范围 |
| API key 缺失 | 提示需要配置的环境变量名，不打印密钥 |
| 模型调用超时或失败 | 记录失败原因，已完成阶段仍可生成报告 |
| skill 文件不存在或读取失败 | 跳过该 skill，并在报告中记录 |
| `--tests` 路径不存在 | 跳过测试关联，继续审查 diff |
| 依赖链路无法静态确认 | 标记为低置信度，交由人工复审 |

---

## 十、扩展路线

| 阶段 | 扩展内容 |
|------|----------|
| v1 | 前端（Vue/TS）爆炸范围 + 代码质量 + Skill 扩展 |
| v2 | 真正运行测试用例（Vitest 集成） |
| v3 | 后端支持（Java/Go/Python） |
| v4 | HTML 报告 + 团队共享 skill 仓库 |
