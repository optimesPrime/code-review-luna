# [后备计划 P3] 多仓 Watch Daemon

**状态：** 后备，IDE 集成或团队部署场景时实施

**价值：** 一个后台进程管理多个仓库的图谱实时更新，支持 config 热更新（无需重启）、子进程自愈、跨平台（Unix daemon 模式 + Windows 前台模式）。

---

## 参考代码（精确位置）

| 功能 | 文件 | 行号 |
|------|------|------|
| TOML 配置加载/保存 | `daemon.py:45-120` | `load_config()` / `save_config()` |
| 双 fork Unix daemon | `daemon.py:122-195` | `daemonize()` |
| Windows 前台模式兼容 | `daemon.py:135-145` | `if sys.platform == "win32"` 分支 |
| 子进程启动/终止 | `daemon.py:198-260` | `_start_watcher()` / `_terminate_child()` |
| Config 热更新 + reconcile | `daemon.py:262-380` | `ConfigWatcher` + `reconcile()` |
| Health checker 自愈（30s） | `daemon.py:382-440` | `_health_loop()` |
| 跨进程状态 JSON | `daemon.py:442-500` | `_save_state()` / `load_state()` |
| CLI 子命令（start/stop/status/add/remove） | `daemon_cli.py:1-200` | 全文 |

**全路径：** `/Users/wangyinlong/code-review-graph/code_review_graph/`

---

## 核心实现要点

### TOML 配置结构
```toml
[daemon]
session_name = "luna-watch"
log_dir = "~/.luna/logs"
poll_interval = 2   # config 变更 polling 间隔（秒）

[[repos]]
path = "/Users/xxx/my-frontend-project"
alias = "frontend"

[[repos]]
path = "/Users/xxx/my-backend-project"
alias = "backend"
```

### 双 Fork Unix Daemon
```python
def daemonize(log_file):
    if sys.platform == "win32":
        write_pid(); return  # Windows 不支持 fork

    if os.fork() > 0: sys.exit(0)   # 第一次 fork
    os.setsid()                       # 成为 session leader
    if os.fork() > 0: sys.exit(0)   # 第二次 fork（防止重获控制终端）

    # 重定向 I/O
    fd = os.open(log_file, os.O_WRONLY | os.O_CREAT | os.O_APPEND)
    os.dup2(fd, sys.stdout.fileno())
    os.dup2(fd, sys.stderr.fileno())
    os.dup2(os.open(os.devnull, os.O_RDONLY), sys.stdin.fileno())

    signal.signal(signal.SIGTERM, lambda s, f: sys.exit(0))
    write_pid()
```

### Config 热更新 Reconcile
```python
def reconcile(self, desired: dict[str, RepoConfig]):
    current = set(self._current_repos)
    desired_keys = set(desired)
    for alias in desired_keys - current:          # 新增仓库 → 启动 watcher
        self._start_watcher(desired[alias])
    for alias in current - desired_keys:           # 删除仓库 → 停止 watcher
        self._terminate_child(alias)
    for alias in desired_keys & current:           # 路径变了 → 重启
        if desired[alias].path != self._current_repos[alias].path:
            self._terminate_child(alias)
            self._start_watcher(desired[alias])
```

### 30s 自愈 Health Checker
```python
def _health_loop(self):
    while not self._stop.is_set():
        self._stop.wait(30)
        for alias, proc in list(self._children.items()):
            if proc.poll() is not None:  # 子进程已退出
                logger.warning("Watcher '%s' died, restarting", alias)
                self._start_watcher(self._current_repos[alias])
```

---

## Luna 实施要点

1. **CLI 子命令**：`luna daemon start` / `luna daemon stop` / `luna daemon status` / `luna daemon add <path>`
2. **先实施单仓 watch**（见 incremental-update-engine 计划书）再扩展到多仓 daemon
3. **跨进程状态文件**：`~/.luna/daemon-state.json` 存 `{alias: {pid, path, last_updated}}`
4. **Windows 支持**：不能 fork，改用 `subprocess.Popen` + `CREATE_NEW_PROCESS_GROUP` 标志
5. **Config 文件位置**：`~/.luna/watch.toml`，与 `~/.luna/config.yaml` 并列

---

## 估算

- TOML config + CLI：100 行，3 天
- 单 repo watch（复用 incremental）：100 行，2 天
- 多 repo daemon + reconcile：200 行，1 周
- Health checker + 跨进程状态：100 行，3 天
- 总工作量：2 周
- 依赖：`tomllib`（Python 3.11+ 内置）或 `tomli`（3.10 fallback）
