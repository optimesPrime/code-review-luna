# 爆炸范围（影响链路）重设计

**日期：** 2026-06-16  
**状态：** 已批准，待实现

---

## 背景

当前"爆炸范围"输出仅展示平铺的文件路径箭头（`a.ts → b.ts → c.ts`），无风险颜色区分、无传播原因，读者无法一眼判断"哪里会出问题"及"为什么"。重设计目标：每个改动独立成块，以树状结构展示完整传播链路，每节点带原因注释。

---

## 目标

- **主要：** 明确"哪里会出问题"（具体 file:line + 原因）
- **主要：** 体现"哪个业务方向受影响"（按 changed_symbol 分块）
- **次要：** 展示影响规模（顶部总数）
- **约束：** 与"审查点命中"互补，不重叠

---

## 视觉设计

### 有 impact_paths 时（完整链路树）

```
💥  影响链路  3 条

  🚨  getUserById
      └── authMiddleware.ts:45   直接解构 userId 字段
          └── apiRouter.ts:88    所有路由经此中间件，全部受影响

  ⚠️  updateUser
      └── userStore.ts:12        初始化时依赖此返回结构
          └── Dashboard.tsx:88   渲染层读取 user.id，可能为空

  💡  formatDate
      └── UserCard.tsx:33        仅展示层受影响，无逻辑风险
```

- 每个 `changed_symbol` 独立成一棵树
- 树根：符号名，颜色/图标取该块最高风险
- 子节点：`file:line` + 一行原因，按路径深度缩进
- 顶部：`💥  影响链路  N 条`

### 无 impact_paths 时（fallback，仅 blast_radius_items）

```
💥  影响链路

  🚨  getUserById
      ├── authMiddleware.ts:45   直接解构 userId
      └── userStore.ts:12        初始化依赖返回结构
```

- 根节点：`changed_symbols[0].name`（或文件名）
- 叶节点：按 risk 排序的 blast_radius_items，最多 8 条

---

## 数据映射

| 展示内容 | 数据来源 |
|---|---|
| 树根符号名 | `report.changed_symbols[].name` 或 `.file` |
| 链路节点文件 | `report.impact_paths[].path[]`，按第一个节点分组 |
| 节点原因 | 优先：`blast_radius_items` 按 `file` 匹配 `reason`；fallback：`impact_paths[].reason`（挂在叶节点） |
| 块级风险图标 | 该组 `impact_paths` 中 `risk` 最高值 |
| 顶部总数 | `len(impact_paths)` 或 `len(blast_radius_items)` |

**分组规则：** `impact_paths` 按 `path[0]`（source 文件）分组；同一 source 的多条路径合并到同一棵树。

---

## 实现范围

### 修改 `terminal_renderer.py`

1. **新函数 `_group_impact_paths(report)`**  
   输入：`report.impact_paths`、`report.changed_symbols`、`report.blast_radius_items`  
   输出：`list[ImpactBlock]`，每个 block 包含：
   - `symbol_name: str`
   - `risk: str`（块最高风险）
   - `chains: list[list[ChainNode]]`（每条路径为一组节点）

2. **数据类 `ChainNode`**（`@dataclass`）  
   - `file: str`
   - `line: int`
   - `reason: str`
   - `risk: str`

3. **新函数 `_render_blast_section(console, report)`**（替换现有同名函数）  
   - 调用 `_group_impact_paths` 获取 blocks
   - 每个 block 用 `rich.Tree` 渲染
   - fallback 逻辑：无 impact_paths 时直接从 blast_radius_items 构建单棵树

### 保留不变

- `build_blast_chain`（现有测试依赖，保留函数签名）
- `build_business_tree`（保留，供将来复用）

---

## 测试计划

在 `tests/test_terminal_renderer.py` 新增 `TestRenderBlastSection` 类：

1. `test_each_symbol_is_independent_block` — 两个不同 source 的 impact_paths 渲染为两棵独立树
2. `test_chain_nodes_show_reason` — 节点原因从 blast_radius_items 匹配
3. `test_fallback_without_impact_paths` — 无 impact_paths 时从 blast_items 构建单棵树
4. `test_risk_icon_reflects_highest_risk_in_block` — 块级图标取最高风险
5. `test_node_reason_fallback_to_path_reason` — blast_items 无匹配时用 path.reason
6. `test_empty_report_renders_nothing` — 无数据时不输出爆炸范围区块
7. 更新 `test_blast_chain_shown_when_impact_paths` — 确认"爆炸范围"和"→"仍在输出中
