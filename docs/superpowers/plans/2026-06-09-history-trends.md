# luna history — 审查历史与风险趋势

**目标：** `luna history` 读取历史报告 JSON，展示过去 N 次审查的风险趋势、频繁被标记的文件、常见问题类型，帮助团队识别长期质量盲区。

---

## 产品行为

```bash
# 展示最近 10 次审查概览
luna history

# 展示某个文件的审查历史
luna history --file src/store/user.ts

# 展示风险趋势（ASCII 折线图）
luna history --trend

# 展示最常被标记的文件 Top 10
luna history --hotspots
```

**`luna history` 默认输出：**

```
🌙 Luna 审查历史 · 最近 10 次

  日期         提交      verdict      🚨  ⚠️  💡  耗时
  2026-06-09   e2737f2   建议修复      3   2   1   3.2s
  2026-06-08   2740cb9   可提交        0   1   2   2.8s
  2026-06-07   0b22530   阻塞提交      5   3   0   4.1s
  ...

**`luna history --hotspots` 输出：**

  高频风险文件 Top 10（近 30 次审查）

  文件                        出现次数   最高风险   最近标记
  src/store/user.ts               8       high      2026-06-09
  src/request.ts                  6       high      2026-06-08
  src/views/Login.vue             4       medium    2026-06-07
```

**`luna history --trend` 输出：**

```
风险趋势（近 30 次）

高风险  ▂▃▅▄▃▂▁▂▃▄
中风险  ▃▄▃▂▃▄▃▃▂▁
低风险  ▅▄▃▄▅▄▃▄▃▂
        ← 旧                新 →
```

---

## 架构

报告 JSON 格式（`reporter.py` 每次审查写入）：

```json
{
  "timestamp": "2026-06-09T14:32:00",
  "commit": "e2737f2",
  "verdict": "建议修复后提交",
  "high": 3, "medium": 2, "low": 1,
  "elapsed": 3.2,
  "items": [
    {"file": "src/store/user.ts", "line": 42, "risk": "high", "issue_type": "state_sync"}
  ],
  "fix_candidates": [...]
}
```

`history_reader.py` 扫描 `{reports_dir}/*.json`，解析后聚合。

```
luna.py
  └── luna history [--file F] [--trend] [--hotspots] [-n N]
        ├── history_reader.py → load_reports(reports_dir, limit)
        ├── history_reader.py → aggregate_hotspots(reports)
        ├── history_reader.py → build_trend(reports)
        └── history_renderer.py → render_*(data)（Rich 表格 / 迷你 sparkline）
```

---

## 文件地图

| 操作 | 路径 | 职责 |
|------|------|------|
| Create | `phases/history_reader.py` | 扫描解析报告 JSON；`load_reports`、`aggregate_hotspots`、`build_trend` |
| Create | `history_renderer.py` | Rich 渲染：概览表格、hotspots 表格、sparkline 趋势图 |
| Modify | `reporter.py` | `save()` 额外写结构化 JSON sidecar（`{timestamp}_report.json`）；含 `commit`、`verdict`、`high/medium/low` 计数、`items` 摘要 |
| Modify | `luna.py` | 新增 `luna history` 子命令 |
| Modify | `runtime_context.py` | 新增 `commit_hash: str`（从 `git rev-parse --short HEAD` 获取） |
| Create | `tests/test_history_reader.py` | 测试报告解析、hotspot 聚合、trend 构建 |

---

## Task 1：reporter.py 写结构化 JSON sidecar

**前提：** 目前 `save()` 只写 Markdown 报告，`latest.json` 只含 fix_candidates（luna fix 计划里已加）。这里要写完整的历史 JSON。

- [ ] 写失败测试：`test_save_writes_json_sidecar_with_risk_counts`
- [ ] 确认测试失败
- [ ] `runtime_context.py` 新增 `commit_hash: str = ""`，在 `luna.py` 里用 `git rev-parse --short HEAD` 赋值
- [ ] `reporter.py` 的 `save()` 在写完 Markdown 后，额外写 `{timestamp}_report.json`：

```python
sidecar = {
    "timestamp": report.timestamp,
    "commit": runtime_ctx.commit_hash,
    "verdict": build_verdict(report).label,   # 复用 terminal_renderer 的 verdict 逻辑
    "high": sum(1 for i in all_items if i.risk == "high"),
    "medium": sum(1 for i in all_items if i.risk == "medium"),
    "low": sum(1 for i in all_items if i.risk == "low"),
    "elapsed": runtime_ctx.elapsed_seconds,
    "items": [{"file": i.file, "line": i.line, "risk": i.risk} for i in all_items],
    "fix_candidates": [dataclasses.asdict(fc) for fc in report.fix_candidates],
}
```

- [ ] 确认测试通过
- [ ] `pytest -q` 全绿
- [ ] commit：`feat: reporter writes structured JSON sidecar for history tracking`

---

## Task 2：实现 `history_reader.py`

- [ ] 写失败测试：
  - `test_load_reports_returns_sorted_by_date(tmp_path)`
  - `test_aggregate_hotspots_counts_files_correctly(tmp_path)`
  - `test_build_trend_returns_counts_per_report(tmp_path)`
  - `test_load_reports_skips_malformed_json(tmp_path)`
- [ ] 确认测试失败
- [ ] 实现 `load_reports(reports_dir: str, limit: int = 30) -> list[dict]`
  - 扫描 `{reports_dir}/*.json`（排除 `latest.json`）
  - 按 timestamp 倒序，取前 `limit` 条
  - 解析失败的文件跳过（不崩溃）
- [ ] 实现 `aggregate_hotspots(reports: list[dict], top_n: int = 10) -> list[dict]`
  - 统计每个 `file` 在所有报告中出现的次数、最高 risk、最近标记时间
  - 按出现次数降序，取前 `top_n`
- [ ] 实现 `build_trend(reports: list[dict]) -> dict`
  - 返回 `{"high": [3,2,1,...], "medium": [...], "low": [...]}`（每报告一个数值，旧→新）
- [ ] 确认测试通过
- [ ] `pytest tests/test_history_reader.py -v` 全绿
- [ ] commit：`feat: history_reader — load reports, aggregate hotspots, build trend`

---

## Task 3：实现 `history_renderer.py`

- [ ] 写失败测试：`test_render_overview_returns_table`（检查无异常，表格含预期列名）
- [ ] 确认测试失败
- [ ] 实现 `render_overview(reports: list[dict]) -> None` — Rich Table，列：日期、提交、verdict（带颜色）、🚨、⚠️、💡、耗时
- [ ] 实现 `render_hotspots(hotspots: list[dict]) -> None` — Rich Table，列：文件、出现次数、最高风险、最近标记
- [ ] 实现 `render_trend(trend: dict) -> None` — 用 Unicode 块字符（▁▂▃▄▅▆▇█）画 sparkline，每级风险一行
- [ ] 确认测试通过
- [ ] `pytest tests/test_history_reader.py -v` 全绿
- [ ] commit：`feat: history_renderer — overview table, hotspots, sparkline trend`

---

## Task 4：`luna history` 子命令

**文件：** `luna.py`

- [ ] 新增子命令：

```python
@main.command("history")
@click.option("-n", "limit", default=10, help="展示最近 N 次审查")
@click.option("--file", "filter_file", default=None, help="按文件过滤")
@click.option("--trend", "show_trend", is_flag=True)
@click.option("--hotspots", "show_hotspots", is_flag=True)
@click.option("--config", "config_path", default=None)
def history_cmd(limit, filter_file, show_trend, show_hotspots, config_path): ...
```

- [ ] 实现：加载配置 → `load_reports` → 按 flag 路由到对应 renderer
- [ ] `--file` 模式：在 `load_reports` 结果中过滤 `items` 含指定文件的报告，展示该文件的历史命中记录
- [ ] 无历史报告时打印提示："还没有审查记录。先运行 `luna --staged` 生成第一份报告。"
- [ ] 写测试：`test_history_cmd_no_reports_prints_hint`
- [ ] 确认测试通过
- [ ] `pytest -q` 全绿
- [ ] commit：`feat: luna history subcommand — overview, hotspots, trend`

---

## Task 5：验证

```bash
pytest -q
python3 luna.py history --help
```

手动验证：

```bash
luna --staged           # 生成几份报告
luna history            # 概览
luna history --trend    # 趋势图
luna history --hotspots # 热点文件
luna history --file src/store/user.ts
```

验收标准：
- 有报告时正确渲染表格
- sparkline 数值与报告数据一致
- `--file` 只展示含该文件的报告
- 无报告时打印友好提示

---

## Non-Goals（本阶段不做）

- Web UI 仪表盘
- 跨仓库汇总
- 团队成员维度统计（谁提交的高风险最多）
- 报告导出 CSV / Excel
