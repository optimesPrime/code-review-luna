# Luna Review 输出重设计 v2

**日期：** 2026-06-13  
**目标：** 实用、好看、容易看到重点、有利于阅读；每条问题内联操作命令（luna fix N / luna detail N）。

---

## 整体结构

输出从上到下共 7 个区块，按出现条件控制显隐：

```
━━━━━━━━━━ 🌙 Luna Review · <project> · <N files> <N lines> · <Ns> ━━━━━━━━━━

  <verdict_icon>  <verdict_label>     🚨 <high>   ⚠️  <medium>   💡 <low>

──────────────────────── 🔴 必须修复 ────────────────────────
  （high 条目展开卡片）

──────────────────────── ⚠️  建议修复 ────────────────────────
  （medium/low 条目展开）

──────────────────────── 💥 爆炸范围 ────────────────────────
  （链路字符串）

──────────────────────── 🔍 审查点命中 ──────────────────────
  （仅命中的审查点）

──────────────────────── 🔬 反驳验证 ────────────────────────
  （仅有过滤误报时）

报告：<report_path>
```

---

## 区块 1：标题行（始终显示）

使用 Rich `Rule`，项目信息内联在标题里：

```
━━━ 🌙 Luna Review · myapp · frontend · 3 files 120 lines · 4.2s ━━━
```

紧接一行 Verdict + 风险数：

```
  🚫  阻塞提交     🚨 3   ⚠️  2   💡 1
```

- Verdict 图标与标签：`🚫 阻塞提交 / ⚠️ 建议修复后提交 / 💡 可提交但建议关注 / ✅ 可提交`
- 风险数字：高风险 bold red，中风险 bold yellow，低风险 bold blue；为 0 时 dim
- **不使用居中大框 Panel**，保持一行平铺

---

## 区块 2：必须修复（有 high 条目时显示）

Section header：`Rule("🔴  必须修复", style="dim")`

每条 high 风险条目展开为 3~4 行卡片：

```
  🚨  <file>:<line>  —  <reason[:80]>
      <suggestion[:120]>（如有建议则显示）
      $ luna fix <N> --preview        ← assist 模式
      $ luna fix <N>                  ← auto 模式
      $ luna detail <N>               ← manual 模式
```

- 命令样式：`bold green`，`$` 前缀方便复制
- 超过 5 条 high 时，展示前 5 条，末尾追加：
  ```
    + <N> 条高风险，运行 luna detail 查看完整报告
  ```

---

## 区块 3：建议修复（有 medium 或 low 条目时显示）

Section header：`Rule("⚠️   建议修复", style="dim")`

**medium 条目**：展开，格式同区块 2（但 icon 用 ⚠️）：

```
  ⚠️   <file>:<line>  —  <reason[:80]>
       <suggestion[:120]>（如有）
       $ luna fix <N>    （🤖 自动修复）
```

**low 条目**：只显示一行，不展开建议：

```
  💡  <file>:<line>  —  <reason[:60]>    $ luna detail <N>
```

---

## 区块 4：爆炸范围（有传播路径时显示）

Section header：`Rule("💥  爆炸范围", style="dim")`

优先使用 `report.impact_paths`，fallback 使用 blast items 的文件列表：

```
  auth.ts → store/user.ts → pages/login.vue → router/index.ts
  store/index.ts → pages/dashboard.vue
```

- 每条路径最多 5 个节点，超出追加 `→ ...`
- 最多显示 3 条路径
- 节点只取文件名（不含路径前缀）

---

## 区块 5：审查点命中（有命中时显示，全通过不显示）

Section header：`Rule("🔍  审查点命中", style="dim")`

每条命中的审查点单行显示：

```
  🚨 权限/登录态    src/auth.ts:18          $ luna detail 1
  🚨 状态同步       src/store/user.ts:42    $ luna detail 2
  ⚠️  异常处理       src/api/login.ts:91     $ luna detail 3
```

- 全部审查点均通过（status == "ok"）时，整个区块不出现
- 命令统一为 `luna detail <对应 fix_candidate 编号>`；若找不到编号则不显示命令

---

## 区块 6：反驳验证（有过滤误报时显示）

Section header：`Rule("🔬  反驳验证 — 已过滤误报", style="dim")`

保持现有紧凑表格（符号 · 位置 · 原始原因删除线 · 反驳理由），无变化。

---

## 区块 7：页脚（始终显示）

```
报告：.luna-reports/2026-06-13_report.md
```

使用 `Rule(f"[dim]报告: {runtime.report_path}[/dim]", style="dim")`。

---

## 删除的区块

| 旧区块 | 原因 |
|--------|------|
| 居中 Verdict Panel（大框） | 太重，命令占屏多；改为一行平铺 |
| 审查点完整 11 行表格 | 7 行 ✅ 噪音；改为只列命中项 |
| 嵌套圆环爆炸地图（build_explosion_map） | 视觉复杂，不如链路字符串直观 |
| 修复队列独立表格区块 | 命令已内联到每条问题卡片，不需重复 |
| Token 使用情况面板 | 无人关心，去掉 |
| 自动发现的审查关注点 Panel | 信息重复，去掉 |

---

## 文件改动范围

| 操作 | 文件 |
|------|------|
| **重写** `_render_rich` | `terminal_renderer.py` |
| **删除** `build_explosion_map`（内部函数，外部无调用） | `terminal_renderer.py` |
| **保留** `build_fix_queue`、`FixCandidate`、`render_token_savings_panel` | `terminal_renderer.py`（luna fix 命令依赖） |
| **新增/更新测试** | `tests/test_terminal_renderer.py` |

---

## 验收标准

肉眼检查（运行 `luna --staged`）：

- [ ] 标题行 + 项目信息合并在一条 Rule 里
- [ ] Verdict 和风险数字同一行，无居中大框
- [ ] 高风险卡片每条有 `$ luna fix N` 或 `$ luna detail N`
- [ ] 超过 5 条高风险时显示 `+N 条` 提示
- [ ] 审查点命中区块只列命中项，全通过时整块不出现
- [ ] 爆炸范围显示链路箭头字符串
- [ ] 无修复队列独立表格
- [ ] 无 Token 使用情况区块
- [ ] 无居中 Verdict Panel
