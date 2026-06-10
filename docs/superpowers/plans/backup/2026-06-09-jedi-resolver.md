# [后备计划 P1] Jedi 二次解析 — 修复 Python 调用链断裂

**状态：** 后备，Python 项目比例高时优先实施

**价值：** tree-sitter 遇到 `svc.authenticate()` 这种"小写 receiver 调用"时无法推断类型，调用链就断了。Jedi 二次解析精确补全这些 CALLS 边，Python 项目的影响范围分析准确率直接翻倍。

---

## 参考代码（精确位置）

| 功能 | 文件 | 行号 |
|------|------|------|
| 主入口 | `jedi_resolver.py:1-50` | `enrich_jedi_calls()` |
| 预筛选目标方法名（降低 Jedi 调用次数 90%） | `jedi_resolver.py:52-95` | `_get_project_method_names()` |
| 遍历 AST 找未追踪的小写 receiver 调用 | `jedi_resolver.py:97-185` | `_find_untracked_method_calls()` |
| 调用 jedi.Script.goto 解析类型 | `jedi_resolver.py:187-260` | `_resolve_calls_in_file()` |
| 找 enclosing function（最内层） | `jedi_resolver.py:262-304` | `_find_enclosing()` |

**全路径：** `/Users/wangyinlong/code-review-graph/code_review_graph/jedi_resolver.py`

---

## 核心实现要点

### 为什么需要二次解析
```python
# tree-sitter 能解析：
self.method()        # → ClassName.method （通过 self 推断）
Klass.static_call()  # → Klass.static_call （显式类名）

# tree-sitter 无法解析（receiver 是小写变量）：
svc = get_service()
svc.authenticate()   # → 只知道 "authenticate"，不知道是哪个类
repo.save(entity)    # → 只知道 "save"
```

### 预筛选的关键优化
```python
# 先查 DB 里所有 project 内的函数名
project_methods = store.get_all_method_names()  # {"authenticate", "save", ...}

# 遍历 AST 时，只对命中 project_methods 的调用才问 Jedi
# "console.log" → "log" 不在 project_methods → 跳过
# "svc.authenticate" → "authenticate" 在 → 问 Jedi
# 效果：Jedi 调用次数降低 90%+
```

### Receiver 过滤条件
```python
# 跳过：
if receiver_text in {"self", "cls", "super", "this"}:
    continue  # 这些 tree-sitter 已经能处理
if receiver_text[0].isupper():
    continue  # 大写开头 = 类名直接调用，tree-sitter 已处理
# 只处理：小写变量名的 receiver（svc, repo, client, db...）
```

### Jedi goto 解析
```python
script = jedi.Script(source=src_text, project=jedi_project)
names = script.goto(line=call_line, column=call_col)
if not names:
    continue
name = names[0]
if not name.module_path:
    continue  # 内置/三方库，跳过
# 只处理 project 内部的定义
try:
    name.module_path.relative_to(repo_root)
except ValueError:
    continue
parent = name.parent()
if parent and parent.type == "class":
    target = f"{rel_path}::{parent.name}.{name.name}"
else:
    target = f"{rel_path}::{name.name}"
store.upsert_edge(EdgeInfo(kind="CALLS", source=enclosing_qname, target=target, ...))
```

---

## Luna 实施要点

1. **可选依赖**：`jedi` 加入 `pyproject.toml` 的 `[project.optional-dependencies]` 的 `enrichment` extra，不强制安装
2. **只在 Python 项目触发**：检测到 `.py` 文件才运行 Jedi resolver
3. **Project root 优化**：取所有 Python 文件最深公共父目录作为 jedi project root，避免扫描整个系统
4. **去重**：同一 `(enclosing, line)` 已有 CALLS 边则跳过（防止重复运行时重复插入）
5. **时机**：在 `build_graph` 完成基础解析后，作为可选的 `enrich` 阶段运行

---

## 估算

- 依赖：`pip install jedi`（纯 Python，无 C 扩展）
- 代码量：约 300 行
- 工作量：1 周
- 注意：jedi 在超大 monorepo 上可能慢（几万 Python 文件），需要文件数量上限保护（`CRG_JEDI_MAX_FILES=5000`）
