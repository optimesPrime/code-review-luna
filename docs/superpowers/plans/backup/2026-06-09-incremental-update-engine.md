# [后备计划 P1] 增量更新引擎 + Watch 模式

**状态：** 后备，可与 sqlite-graph-store 同步实施（Task 2 增量更新已在该计划中，本计划扩展 watch 和多跳依赖发现）

**价值：** 文件保存时自动重新解析，发现受影响的依赖文件也一并更新，luna 始终保持最新图谱，无需手动触发。

---

## 参考代码（精确位置）

| 功能 | 文件 | 行号 |
|------|------|------|
| 文件 SHA-256 hash 比对跳过 | `incremental.py:245-280` | `incremental_update()` 核心循环 |
| 2-hop 依赖发现 | `incremental.py:757-825` | `find_dependents()` + `_single_hop_dependents()` |
| 并行解析（ProcessPool + Thread 自适应） | `incremental.py:976-1050` | `_parse_single_file()` + `_make_executor()` |
| Watch + 300ms debounce | `daemon.py:250-380` | `GraphUpdateHandler` + `_flush()` |
| Windows MCP stdio 死锁修复 | `incremental.py:968-975` | `_select_executor_kind()` |
| Stale 文件清理 | `incremental.py:180-215` | `full_build()` 开头段 |
| gitignore + 二进制文件过滤 | `incremental.py:890-935` | `_should_ignore()` |
| DependentList（带 .truncated 旗标） | `incremental.py:740-757` | `class DependentList` |

**全路径前缀：** `/Users/wangyinlong/code-review-graph/code_review_graph/`

---

## 核心实现要点

### 文件 Hash + 跳过
```python
raw = abs_path.read_bytes()
fhash = hashlib.sha256(raw).hexdigest()
existing = store.get_nodes_by_file(str(abs_path))
if existing and existing[0].file_hash == fhash:
    continue  # 未变化，跳过
# 否则：删旧节点/边 → 重新 parse → 插入新节点/边
```

### 2-hop 依赖发现
```python
def find_dependents(store, changed_files, max_hops=2, max_files=500):
    frontier = set(changed_files)
    visited = set(changed_files)
    for _ in range(max_hops):
        next_frontier = set()
        for f in frontier:
            for dep in _single_hop_dependents(store, f):
                if dep not in visited:
                    next_frontier.add(dep)
                    visited.add(dep)
        frontier = next_frontier
        if len(visited) > max_files:
            return DependentList(list(visited), truncated=True)
    return DependentList(list(visited - set(changed_files)))
```

### Watch + Debounce
```python
class GraphUpdateHandler(FileSystemEventHandler):
    def __init__(self, store, debounce_ms=300):
        self._timer = None
        self._pending = set()
        self._lock = threading.Lock()

    def on_modified(self, event):
        with self._lock:
            self._pending.add(event.src_path)
            if self._timer: self._timer.cancel()
            self._timer = threading.Timer(
                self._debounce_ms / 1000, self._flush)
            self._timer.start()

    def _flush(self):
        with self._lock:
            files = set(self._pending)
            self._pending.clear()
        # 重新 parse files + find_dependents(files)
        incremental_update(self.store, changed_files=files)
```

### Executor 自适应（Windows MCP stdio 死锁修复）
```python
def _select_executor_kind() -> str:
    if sys.platform == "win32" and not sys.stdin.isatty():
        return "thread"  # MCP stdio on Windows → 避免 pipe handle 继承
    return "process"
```

---

## Luna 实施要点

1. **`luna watch` 子命令**：在 `luna.py` 加 `@cli.command("watch")`，启动 `GraphUpdateHandler`，保持图谱实时更新
2. **300ms debounce**：IDE 批量保存多个文件只触发一次 flush
3. **find_dependents 上限 500 文件**：改一个 shared util 时防止递归爆炸
4. **跳过 node_modules/dist/build**：直接复用 `_should_ignore` 的 gitignore 解析逻辑
5. **结合 sqlite-graph-store.md 的 Task 2**：hash 比对逻辑共用同一套实现

---

## 估算

- 核心（full_build + incremental + hash）：300 行（已在 sqlite-graph-store Task 2）
- find_dependents 2-hop：50 行
- Watch + debounce + `luna watch` 命令：200 行
- ignore 系统：50 行
- 工作量：1.5-2 周（与 sqlite-graph-store 合并实施更高效）
