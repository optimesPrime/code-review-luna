# GitLab MR 集成 — 把 Luna 审查结果发到 Merge Request

**目标：** `luna review --mr <MR_IID>` 从 GitLab 拉取 MR diff，运行 Luna 全流程审查，把结果以 inline review comments 形式发到 GitLab MR 上。GitHub 支持作为后续计划。

---

## 产品行为

```bash
# 审查当前项目的 MR #42，发 inline 评论
luna review --mr 42

# 只跑审查，不发评论（dry-run）
luna review --mr 42 --dry-run

# 指定项目（多项目场景）
luna review --mr 42 --project mygroup/myrepo
```

GitLab MR 上的效果：

```
Luna Review Bot                                      🤖
─────────────────────────────────────────────────────
🚫 阻塞提交 · 🚨 3 高风险  ⚠️ 2 中风险  💡 1 低风险

[查看完整报告](链接)
```

每条高/中风险 item 在对应代码行生成 inline 讨论：

```
src/store/user.ts +42           Luna Review Bot 🤖
─────────────────────────────────────────────────────
⚠️ 中风险 · 状态同步
store 初始化时机影响首个请求的 tradeUserId 上下文。
建议在 login() resolve 后再触发 router.replace。
置信度: high
```

---

## 架构

```
luna.py
  └── luna review --mr N
        ├── gitlab_client.py → get_mr_diff(mr_iid)          # 拉 MR diff
        ├── 走 Luna 全流程审查（复用现有 pipeline）
        ├── gitlab_client.py → post_mr_summary(mr_iid, report)   # 发总结评论
        └── gitlab_client.py → post_inline_comments(mr_iid, items, diff_meta)  # 发 inline 评论
```

GitLab API 使用 `/api/v4`：
- `GET /projects/:id/merge_requests/:mr_iid/diffs` — 拉 diff
- `POST /projects/:id/merge_requests/:mr_iid/discussions` — 发 inline 讨论（带 `position` 定位到具体行）

inline 评论的 `position` 结构：
```json
{
  "base_sha": "...", "start_sha": "...", "head_sha": "...",
  "position_type": "text",
  "new_path": "src/store/user.ts",
  "new_line": 42
}
```

---

## 配置扩展

`~/.luna/config.yaml` 新增：

```yaml
gitlab:
  url: https://gitlab.example.com   # 私有部署地址，默认 https://gitlab.com
  token_env: GITLAB_TOKEN           # Personal Access Token 环境变量名
  project_id: mygroup/myrepo        # 默认项目，--project 可覆盖
  bot_note_prefix: "🌙 Luna Review" # 评论前缀，便于识别和清理
  post_inline: true                 # 是否发 inline 评论，false 只发汇总
  min_risk: medium                  # 只发 medium 及以上的 inline 评论
```

---

## 文件地图

| 操作 | 路径 | 职责 |
|------|------|------|
| Create | `phases/gitlab_client.py` | GitLab API 封装：`get_mr_diff`、`get_mr_meta`、`post_summary_comment`、`post_inline_comment` |
| Create | `phases/mr_reviewer.py` | 编排 MR 审查流程；把 Luna items 映射到 GitLab diff position |
| Modify | `luna.py` | 新增 `luna review` 子命令 |
| Modify | `config.py` | 新增 `GitLabConfig` dataclass |
| Create | `tests/test_gitlab_client.py` | mock GitLab API，测试请求构造和响应解析 |
| Create | `tests/test_mr_reviewer.py` | 测试 item → position 映射逻辑 |

---

## Task 1：GitLab API 客户端

**文件：** `phases/gitlab_client.py`

- [ ] 写失败测试：`test_get_mr_diff_parses_response`、`test_post_inline_comment_builds_correct_payload`（mock `requests.get/post`）
- [ ] 确认测试失败
- [ ] 实现 `GitLabClient(url, token, project_id)`：

```python
class GitLabClient:
    def get_mr_meta(self, mr_iid: int) -> dict:
        """返回 MR 基本信息，含 diff_refs（base/start/head sha）。"""

    def get_mr_diff(self, mr_iid: int) -> str:
        """返回 unified diff 字符串（拼接所有文件 diff）。"""

    def post_summary_comment(self, mr_iid: int, body: str) -> None:
        """在 MR 顶部发一条总结评论。"""

    def post_inline_comment(
        self,
        mr_iid: int,
        body: str,
        file_path: str,
        new_line: int,
        diff_refs: dict,
    ) -> None:
        """在指定文件行发 inline 讨论。"""
```

- [ ] 错误处理：401（token 无效）、403（无权限）、404（MR 不存在）分别打印明确提示
- [ ] 确认测试通过
- [ ] `pytest tests/test_gitlab_client.py -v` 全绿
- [ ] commit：`feat: add GitLabClient for MR diff fetch and inline comment posting`

---

## Task 2：item → GitLab diff position 映射

**文件：** `phases/mr_reviewer.py`

Luna item 有 `file` 和 `line`，GitLab inline comment 需要 `new_path`、`new_line` 加上 `diff_refs`。

- [ ] 写失败测试：`test_map_item_to_position_returns_correct_new_line`、`test_map_item_skips_deleted_file`
- [ ] 确认测试失败
- [ ] 实现 `map_items_to_positions(items, diff_refs) -> list[tuple[item, position]]`
  - 过滤掉在 diff 中被删除的文件（`new_path` 为空）
  - `new_line` 直接取 `item.line`（GitLab 接受绝对行号）
  - 行号超出文件范围时跳过（不发 inline，转为汇总评论）
- [ ] 确认测试通过
- [ ] 实现 `build_summary_comment(report) -> str`：生成 Markdown 格式总结
- [ ] 实现 `build_inline_comment(item) -> str`：生成单条 inline 评论 Markdown
- [ ] commit：`feat: mr_reviewer — map Luna items to GitLab diff positions`

---

## Task 3：`luna review` 子命令

**文件：** `luna.py`

- [ ] 新增子命令：

```python
@main.command("review")
@click.option("--mr", "mr_iid", type=int, required=True, help="GitLab MR IID")
@click.option("--project", default=None, help="覆盖配置中的 project_id")
@click.option("--dry-run", is_flag=True, help="只审查，不发评论")
@click.option("--config", "config_path", default=None)
def review_cmd(mr_iid, project, dry_run, config_path):
    """从 GitLab MR 拉取 diff 并发布审查评论。"""
```

- [ ] 实现流程：
  1. 加载配置，校验 `cfg.gitlab.token_env` 环境变量存在
  2. `GitLabClient.get_mr_diff(mr_iid)` 拿 diff
  3. 走 Luna 全流程（复用 `cli()` 中的 pipeline，抽取为可复用函数）
  4. `dry_run=True` 时只打印终端结果，不调 GitLab post API
  5. 否则 `post_summary_comment` + `post_inline_comment`（按 `cfg.gitlab.min_risk` 过滤）
- [ ] 写测试：`test_review_cmd_dry_run_does_not_call_post`
- [ ] 确认测试通过
- [ ] `pytest -q` 全绿
- [ ] commit：`feat: luna review --mr subcommand with GitLab inline comment posting`

---

## Task 4：config.py 扩展

**文件：** `config.py`

- [ ] 新增 `GitLabConfig` dataclass：

```python
@dataclass
class GitLabConfig:
    url: str = "https://gitlab.com"
    token_env: str = "GITLAB_TOKEN"
    project_id: str = ""
    bot_note_prefix: str = "🌙 Luna Review"
    post_inline: bool = True
    min_risk: str = "medium"    # "high" | "medium" | "low"
```

- [ ] 在 `Config` 中加 `gitlab: GitLabConfig = field(default_factory=GitLabConfig)`
- [ ] 写测试：`test_config_loads_gitlab_section`
- [ ] 确认测试通过
- [ ] `pytest tests/test_config.py -v` 全绿
- [ ] commit：`feat: add GitLabConfig to config dataclass`

---

## Task 5：验证

```bash
pytest -q
python3 luna.py review --help
```

手动验证（需要可访问的 GitLab 实例和真实 MR）：

```bash
export GITLAB_TOKEN=glpat-xxxx
luna review --mr 1 --dry-run          # 不发评论，只打印结果
luna review --mr 1                    # 真实发评论
```

验收标准：
- dry-run 输出和普通 `luna --staged` 格式一致
- MR 顶部出现总结评论
- 高/中风险 item 出现在对应代码行的 inline 讨论
- 401/403/404 时打印明确错误提示

---

## Non-Goals（本阶段不做）

- GitHub 支持（后续独立计划）
- 自动关闭旧的 Luna 评论再重新发（去重逻辑）
- MR 审查通过后自动 approve
- Webhook 触发（push 时自动审查）
