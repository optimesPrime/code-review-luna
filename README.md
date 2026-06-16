# Luna — AI 代码审查 CLI

<p align="center">
  <img src="assets/avatar.jpeg" width="120" height="120" style="border-radius:50%; object-fit:cover;" />
</p>
<h1 align="center">我chavy&nbsp;写代码给我写好了呀！</h1>


> 不是把 diff 丢给大模型，而是先用静态分析构建**结构化证据链**，再基于证据让 AI 做出可追溯的判断。

```bash
luna                        # 审查工作区改动
luna --staged               # 只审查已暂存的内容
luna --since main           # 审查相对 main 的全部差异
```

[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue)](https://python.org)
[![License MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

---

## 为什么要做 Luna

现有 AI 审查工具普遍有两个问题：

1. **缺乏代码理解** — 把原始 diff 交给 LLM，模型看不到改动符号被谁调用、影响多远，只能泛泛地"代码 review 一下"。
2. **误报率高** — 没有反向验证，模型自信地给出错误风险判断，工程师反而要花时间甄别。

Luna 的做法是：先把代码"翻译成案卷"，再让 AI 基于证据判案；判完之后还跑一轮反驳验证，主动过滤掉误报。

---

## 核心亮点

### 1. tree-sitter 驱动的符号定位

Luna 用 tree-sitter AST 精确识别本次改动涉及的函数、组件、方法，而不是靠正则猜行号。支持 TypeScript / JavaScript / Vue / Python / Java / Go / C# / PHP / C++，解析结果缓存到 SQLite 供后续增量复用。

### 2. 依赖图 + BFS 影响链路追踪

从改动符号出发，沿项目内的 import/export 或调用关系做 BFS，最多 3 层，自动识别哪些文件、组件、接口被间接影响。前端和后端分别有独立的图引擎。

**前端**：扫描 `.vue / .ts / .tsx / .js / .jsx`，构建 import/export 依赖图；  
**后端**：解析 7 种语言的路由注册、ORM 调用、权限装饰器，识别接口级的改动范围。

| 语言 | 框架重点 |
|------|----------|
| C# | `[Authorize]`、EF Core 事务、`SaveChanges` |
| Java | `@Transactional`、`@PreAuthorize`、JPA 写操作 |
| Python | FastAPI `Depends`、Django ORM、Flask 路由 |
| Node.js | Express / NestJS 路由、`AuthGuard`、Prisma 事务 |
| Go | Gin/Echo handler、`db.`、goroutine 边界 |
| PHP | Laravel `Route::`、`Policy`、`DB::transaction` |
| C++ | 指针生命周期、`std::move`、锁与线程边界 |

### 3. 调用方上下文注入（Caller Context Injection）

对每个改动符号，Luna 自动 grep 出项目内所有调用点，并注入 ±5 行代码片段。LLM 能看到"这个函数被哪里调用、传了什么参数"，大幅降低因信息盲区导致的误报。

```
改动符号: authCheck(userId)
调用点 1: pages/trade/index.vue:88    → activated() 中直接调用，无兜底
调用点 2: composables/useOrder.ts:34  → 传入 this.$route.query.userId（未校验）
```

### 4. Adversarial Verify — 主动过滤误报

LLM 的"高风险低置信度"发现往往是噪音。Luna 发现这类条目后，会自动触发一轮**反驳验证**：另起一个 LLM 调用，专门尝试证伪这个风险判断。通过的才留下，未通过的在输出中单独展示、标记为"已过滤误报"。

### 5. Hybrid 语义增强检索

依赖图 BFS 只能沿静态 import 边传播，动态调用、跨模块的语义关联会被漏掉。Luna 用 SQLite FTS5 对代码块做全文检索，与 BFS 结果做 RRF 融合排序，补充 BFS 找不到的间接影响文件。

### 6. 历史趋势分析

每次审查自动保存结构化报告（`.luna-reports/*.json`）。运行 `luna history` 可查看：
- 各版本风险趋势曲线
- 历史上频繁出现问题的"高风险文件"
- 近期改动的质量变化

### 7. GitLab MR 集成

```bash
luna review --mr 42        # 拉取 MR diff → 分析 → 发布 inline 审查评论
```

支持将审查结果直接作为 MR 评论发回 GitLab，每条问题对应代码行级别的注释。

### 8. 多 AI 提供商，支持中转站

三路路由，一份配置搞定：

```yaml
api:
  provider: anthropic          # 直连 Anthropic
  # provider: openai           # 直连 OpenAI
  # base_url: https://proxy/v1 # 任意 OpenAI 兼容中转站
  model: claude-sonnet-4-6
  key: sk-xxx
```

运行 `luna switch` 交互式切换，不用手动编辑文件。

---

## 审查结果示例

```
🌙  Luna Review · web-trad-system · frontend · staged · 8 files · 126 lines · 3.2s

裁决：阻塞提交    🚨 3 高危   ⚠️ 2 中危   💡 1 低危

─────────────────────────────── 🔴 必须修复 ───────────────────────────────

  ● X-Trade-UserId 首个请求未注入，下游接口将收到空 userId

    文件    request.ts · L42
    原因    interceptors 中仅在 token 存在时注入 header，首个并发请求 token 尚未写入
            store，导致 header 缺失
    建议    改用同步读取 localStorage 或在请求前等待 token ready
    $ luna fix 1 --preview   $ luna fix 1 🤖   $ luna detail 1

─────────────────────────────── ⚠️ 建议修复 ───────────────────────────────

  ● 登录失败后 loading 状态未恢复，UI 永久 loading

    文件    Login.vue · L74
    $ luna fix 2 --preview   $ luna fix 2 🤖

─────────────────────────── 💥 影响链路  4 条 ──────────────────────────────

  ⚠️  src/request/ interceptors.ts
  └── 📁  pages/trade/
        └── ⚠️  index.vue  L88
               activated 中调用了 authCheck，变更后权限校验可能失效
            └── 📁  pages/combination/
                  └── ⚠️  record.vue  L226
                         通过 import 依赖 trade/index，策略代码字段受影响

─────────────────────────── 🔬 反驳验证 ────────────────────────────────────

  已过滤 1 条误报（置信度低，反驳通过）：
  · useTradeStore 状态同步 — 调用方均有独立初始化，实际无影响
```

---

## 快速上手

### 安装

```bash
git clone https://github.com/yourname/luna.git
cd luna
pip install -e .
```

### 配置

```bash
luna switch   # 交互式配置 provider / model / API key
```

或直接编辑 `~/.luna/config.yaml`：

```yaml
api:
  provider: anthropic
  model: claude-sonnet-4-6
  key: sk-ant-xxxxx
```

### 第一次审查

```bash
# 在你的项目目录里
cd /your/project

luna --staged          # 审查已 add 的内容
luna --since main      # 审查相对 main 的全部差异
luna                   # 审查全部未提交改动
```

---

## 命令速查

### 主命令

```bash
luna [OPTIONS]
```

| 选项 | 说明 |
|------|------|
| `--staged` | 只审查 `git add` 的内容 |
| `--since <ref>` | 审查相对某个 ref 的改动，如 `main`、`HEAD~3` |
| `--type frontend\|backend\|fullstack` | 覆盖自动检测的项目类型 |
| `--phase blast\|quality` | 只跑单个分析阶段 |
| `--apply` | 开启可写入模式（仍需逐条确认） |
| `--interactive` | 逐条确认修复建议 |
| `--tests <path>` | 导入测试文件作为上下文 |
| `--format json` | 输出 JSON，适合 CI 消费 |
| `--quiet` | 只输出摘要行 |
| `--details` | 传完整 diff（token 消耗更多，适合复杂改动） |

### 子命令

| 命令 | 说明 |
|------|------|
| `luna fix <N>` | 应用修复队列第 N 条（自动生成 patch） |
| `luna fix <N> --preview` | 只展示 diff，不写入文件 |
| `luna fix <N> --yes` | 跳过确认直接应用 |
| `luna detail <N>` | 查看第 N 条发现的完整详情（证据、建议） |
| `luna history` | 查看历史审查趋势 |
| `luna static` | 静态检查：DB 迁移风险 + API 契约破坏性变更（无 LLM，极快） |
| `luna switch` | 交互式切换 AI 提供商 / 模型 |
| `luna gitlab` | 配置 GitLab 连接信息 |
| `luna review --mr <IID>` | 拉取 MR diff 并发布 inline 审查评论 |
| `luna install-hook` | 安装为 git pre-push hook |
| `luna uninstall-hook` | 卸载 git hook |

---

## 配置文件详解

默认路径：`~/.luna/config.yaml`

```yaml
api:
  provider: anthropic          # anthropic | openai | proxy（中转站）
  model: claude-sonnet-4-6
  key: sk-xxx                  # API Key 直接写入，或设置环境变量 ANTHROPIC_API_KEY
  base_url: ""                 # 中转站地址，非空时自动切换为 proxy 模式

gitlab:
  url: https://gitlab.example.com
  token: glpat-xxx
  project_id: "123"
  min_risk: medium             # 低于此风险等级的发现不发评论

review:
  project_type: auto           # auto | frontend | backend | fullstack
  language: zh                 # 输出语言
  max_diff_chars: 120000       # 最大 diff 字符数

backend:
  enabled: true
  languages: [csharp, java, python, nodejs, go, php, cpp]
  max_depth: 4

privacy:
  ignore: [node_modules, .git, dist, build]
  redact_patterns:
    - Bearer\s+[A-Za-z0-9._-]+
    - AKIA[0-9A-Z]{16}

reports:
  output_dir: .luna-reports

skills: []                     # 团队规则文件列表
```

### 挂载团队规则

```yaml
skills:
  - name: api-conventions
    path: ./team-rules/api.md
  - name: security-checklist
    path: ./team-rules/security.md
```

Luna 会把规则内容注入 system prompt，让审查结果符合你们的代码标准。

---

## 技术架构

```
luna.py                              # CLI 入口（Click），编排全流程
│
├── 静态分析层
│   ├── phases/symbol_locator.py     # tree-sitter AST 符号定位
│   ├── phases/context_graph.py      # 前端 import/export 依赖图（SQLite 缓存）
│   ├── phases/sqlite_graph.py       # SQLite 图持久化 + BFS 查询
│   ├── phases/risk_propagation.py   # 前端 BFS 风险传播（最多 3 层）
│   ├── phases/hybrid_search.py      # BFS + FTS5 全文检索 RRF 融合
│   ├── phases/caller_context.py     # 调用方上下文采样（±5 行）
│   ├── phases/context_pack.py       # 前端上下文打包（JSON 结构化证据）
│   ├── phases/migration_analyzer.py # DB 迁移静态检查
│   ├── phases/api_change_detector.py# API 契约破坏性变更检测
│   │
│   └── 后端管线
│       ├── phases/backend_graph_engine.py      # 后端代码图构建
│       ├── phases/backend_risk_propagation.py  # 后端 BFS 风险传播
│       ├── phases/backend_language_profiles.py # 7 种语言配置
│       ├── phases/backend_language_adapter.py  # 语言适配器基类
│       └── phases/adapters/                    # 各语言适配器实现
│
├── LLM 审查层
│   ├── phases/blast_radius.py       # 爆炸范围审查（结构化上下文 → LLM）
│   ├── phases/code_quality.py       # 代码质量审查
│   ├── phases/backend_review.py     # 后端专项审查
│   └── phases/adversarial_verifier.py # 反驳验证（主动过滤误报）
│
├── 输出层
│   ├── terminal_renderer.py         # Rich 终端渲染（影响链路树、问题卡片）
│   ├── reporter.py                  # Markdown 报告 + JSON sidecar
│   ├── history_renderer.py          # 历史趋势渲染
│   └── luna_fix.py                  # 修复 patch 生成与应用
│
├── 集成层
│   ├── phases/gitlab_client.py      # GitLab API 客户端
│   ├── phases/mr_reviewer.py        # MR diff 拉取 + inline 评论发布
│   └── api_client.py                # Anthropic / OpenAI 统一封装（三路路由）
│
└── 配置与工具
    ├── config.py                    # 配置加载（YAML）
    ├── skill_loader.py              # 团队规则加载
    └── runtime_context.py           # 运行时上下文（项目信息、耗时等）
```

**数据流：**

```
git diff
  → symbol_locator（AST 定位改动符号）
  → context_graph（依赖图）+ hybrid_search（语义增强）
  → risk_propagation（BFS 影响链路）
  → caller_context（调用方上下文采样）
  → context_pack（结构化 JSON 上下文）
  → blast_radius LLM（爆炸范围审查）
  → adversarial_verifier（反驳验证，过滤误报）
  → code_quality LLM（代码质量审查）
  → backend_review LLM（后端专项审查，可选）
  → terminal_renderer（终端输出）+ reporter（文件报告）
```

---

## 依赖

- **Python 3.11+**
- **tree-sitter** 及各语言 grammar（随 `pip install` 自动安装）
- **Rich** — 终端渲染
- **anthropic SDK** 或 **openai SDK**（按使用的 provider 安装）
- **PyYAML** — 配置文件解析
- **Click** — CLI 框架

---

## 开发与贡献

```bash
# 安装开发依赖
pip install -e ".[dev]"

# 运行测试
pytest

# 运行单个测试文件
pytest tests/test_blast_radius.py -v
```

---

## License

MIT
