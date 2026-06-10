# Luna — AI 代码审查助手

Luna 是一款面向工程团队的 AI 代码审查 CLI 工具。它不是把 diff 直接丢给大模型，而是先用静态分析构建**结构化上下文包**，再基于证据链让 AI 做出可追溯的判断。

```
luna --staged
luna --since main --type fullstack
luna --interactive --apply
```

---

## 核心亮点

### 1. 先分析，再审查

普通工具把 diff 原文交给 LLM，Luna 多做一步：用静态分析把改动"翻译"成结构化证据，再把证据给 AI 看。

- **符号定位**：用 tree-sitter AST 精确找出本次改动涉及哪些函数、组件、方法，而不是靠正则猜行号。
- **代码关系图**：扫描整个项目，构建 import/export 依赖图（前端）或调用关系图（后端），缓存复用。
- **风险传播**：从改动符号出发做 BFS，沿依赖边扩散，识别最多 3 层的间接影响链路，自动标注高风险路径。
- **上下文打包**：把符号、链路、关联测试、团队规则汇成一份 JSON，作为 LLM 的"案卷材料"。

### 2. 前后端双管线，自动识别项目类型

Luna 能区分前端和后端项目，不会用后端规则审查 Vue 组件，也不会用前端规则审查 Java 接口。

**前端**（`.vue / .tsx / .jsx / .ts / .js`）：
- Vue 3（Pinia、defineComponent）、React（hooks、store）
- 检查状态同步、请求头传递、路由跳转、登录态保护、测试覆盖等

**后端**（7 种语言全覆盖）：

| 语言 | 框架 | 重点关注 |
|------|------|----------|
| C# | ASP.NET Core | `[Authorize]`、`[HttpPost]`、EF Core 事务、`SaveChanges` |
| Java | Spring Boot / MVC | `@Transactional`、`@PreAuthorize`、JPA 写操作 |
| Python | FastAPI / Django / Flask | 路由装饰器、`Depends`、ORM `.save()` / `.delete()` |
| Node.js | Express / NestJS | 路由注册、`AuthGuard`、Prisma 事务 |
| Go | Gin / Echo / Fiber | Handler 注册、`db.`、goroutine 边界、mutex |
| PHP | Laravel / Symfony | `Route::`、`Policy`、`DB::transaction`、`FormRequest` |
| C++ | gRPC / HTTP handler | 指针生命周期、`std::move`、锁与线程边界 |

`project_type: auto` 时自动检测：diff 中含 `.vue/.tsx/.jsx` 且无后端专有扩展 → 纯前端，跳过后端管线；两者都有 → fullstack，双管线并行。也可用 `--type` 强制指定。

### 3. 终端输出是一个风险指挥台

Luna 跑完后不逐条弹问题，而是在终端一次性呈现完整的审查结果。

```
🌙 Luna Review

🚫 阻塞提交

项目: web-trad-system · 类型: frontend · staged · 8 files / 126 lines · 3.2s

🚨 3   ⚠️ 2   💡 1

 审查点命中
┌──────────────┬──────┬──────────────────────────────┬───────────────┬────────┐
│ 审查点       │ 状态 │ 风险说明                     │ 证据          │ 修复   │
├──────────────┼──────┼──────────────────────────────┼───────────────┼────────┤
│ 请求上下文   │ 🚨高 │ X-Trade-UserId 首个请求缺失  │ request.ts:42 │ manual │
│ 异常处理     │ ⚠️中 │ 登录失败后 loading 未恢复    │ Login.vue:74  │ auto   │
│ 权限/登录态  │ ✅   │ 未发现明显风险               │ -             │ -      │
└──────────────┴──────┴──────────────────────────────┴───────────────┴────────┘

 业务爆炸地图
╔═══════════════════ tradeUserId ═══════════════════╗
║  ┌─────────────────────┐  ┌──────────────────┐  ║
║  │ 🚨 X-Trade-UserId   │  │ 🚨 下单接口      │  ║
║  │ request.ts:42       │  │ order.ts:88      │  ║
║  └─────────────────────┘  └──────────────────┘  ║
╚═══════════════════════════════════════════════════╝

 修复队列
 # │ 模式     │ 影响   │ 说明                    │ 命令
 1 │ 👤 手工  │ 🚫阻塞 │ 确认 tradeUserId 初始化 │ luna detail 1
 2 │ 🤖 自动  │ ⚡高价值│ 补 loading 恢复逻辑    │ luna fix 2 --apply
 3 │ 🔧 辅助  │ 💬建议 │ 补账号切换断言          │ luna fix 3 --preview
```

- **裁决**：阻塞提交 / 建议修复后提交 / 可提交但建议关注 / 可提交，四档明确结论
- **审查点矩阵**：9 类预定义检查点（请求上下文、状态同步、页面跳转、异常处理、权限、测试覆盖、类型/空值、样式、性能），关键字自动命中
- **嵌套爆炸地图**：以改动符号为中心，high → medium → low 依次向外扩散，直观呈现影响范围
- **修复队列**：auto（可一键应用）/ assist（生成 patch 预览）/ manual（人工决策），清晰标注操作方式

### 4. 支持 Claude 和 GPT，支持中转地址

```yaml
api:
  provider: anthropic       # 或 openai
  model: claude-sonnet-4-6  # 或 gpt-4o
  base_url: https://your-proxy/v1   # 可选，支持任意 OpenAI 兼容中转
```

运行 `luna switch` 交互式切换 provider、模型和 base_url，无需手动编辑配置文件。

### 5. 团队规则即插即用

在配置文件中挂载 Markdown 格式的团队规约，Luna 会把它们注入 system prompt，让审查结果符合你们的代码标准：

```yaml
skills:
  - name: api-conventions
    path: ./team-rules/api.md
  - name: security-checklist
    path: ./team-rules/security.md
```

---

## 快速上手

```bash
pip install -e .

# 配置 API Key
export ANTHROPIC_API_KEY=sk-...
# 或
export OPENAI_API_KEY=sk-...

# 首次运行，交互式配置
luna switch

# 审查暂存区改动
luna --staged

# 审查相对 main 的全部改动
luna --since main

# 交互式逐条确认，允许应用修复
luna --interactive --apply
```

---

## 常用命令

| 命令 | 说明 |
|------|------|
| `luna` | 审查工作区未 commit 的改动 |
| `luna --staged` | 只审查 `git add` 的内容 |
| `luna --since main` | 审查相对 main 的全部差异 |
| `luna --type fullstack` | 强制按全栈项目审查 |
| `luna --phase blast` | 只跑爆炸范围分析 |
| `luna --phase quality` | 只跑代码质量检查 |
| `luna --interactive --apply` | 交互式审查，允许写入修复 |
| `luna --tests tests/` | 导入测试用例作为上下文 |
| `luna --format json` | 输出 JSON，便于 CI 集成 |
| `luna --quiet` | 只输出摘要 |
| `luna switch` | 切换 AI 提供商 / 模型 |

---

## 配置文件示例

默认路径：`~/.luna/config.yaml`

```yaml
api:
  provider: anthropic
  model: claude-sonnet-4-6
  api_key_env: ANTHROPIC_API_KEY

review:
  project_type: auto        # frontend | backend | fullstack | auto
  language: zh
  max_diff_chars: 120000

backend:
  enabled: true
  languages: [csharp, java, python, nodejs, go, php, cpp]
  max_depth: 4

privacy:
  redact_patterns:
    - Bearer\s+[A-Za-z0-9._-]+
    - AKIA[0-9A-Z]{16}

reports:
  output_dir: ./.luna-reports

skills: []
```

---

## 技术架构

```
luna.py                          # CLI 入口，编排全流程
├── phases/symbol_locator.py     # tree-sitter 改动符号定位
├── phases/context_graph.py      # 前端 import/export 依赖图
├── phases/risk_propagation.py   # 前端 BFS 风险传播
├── phases/context_pack.py       # 前端上下文打包
├── phases/blast_radius.py       # 爆炸范围 LLM 审查
├── phases/code_quality.py       # 代码质量 LLM 审查
├── phases/backend_graph_engine.py      # 后端代码图构建引擎
├── phases/backend_risk_propagation.py  # 后端 BFS 风险传播
├── phases/backend_review.py            # 后端专项 LLM 审查
├── phases/adapters/                    # 7 种语言适配器
│   ├── csharp_adapter.py
│   ├── java_adapter.py
│   ├── python_adapter.py
│   ├── nodejs_adapter.py
│   ├── go_adapter.py
│   ├── php_adapter.py
│   └── cpp_adapter.py
├── terminal_renderer.py         # Rich 终端渲染
├── reporter.py                  # Markdown 报告生成
└── api_client.py                # Anthropic / OpenAI 统一封装
```

---

## 依赖

- Python 3.11+
- [tree-sitter](https://tree-sitter.github.io/) 及各语言 grammar 包（随 `pip install` 自动安装）
- [Rich](https://github.com/Textualize/rich)（终端渲染）
- anthropic SDK 或 openai SDK（按使用的 provider）
