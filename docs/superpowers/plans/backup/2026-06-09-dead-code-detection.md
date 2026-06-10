# [后备计划 P2] 死代码检测 + 重命名预览

**状态：** 后备，sqlite-graph-store + execution-flows 完成后实施

**价值：** 精确检测无入边的"死代码"，核心价值在于**9 类反误报规则**（Pydantic schema、Angular DI、CDK Stack、abstract method override...）——这是无法从零快速积累的工程经验，直接从参考代码搬移。

---

## 参考代码（精确位置）

| 功能 | 文件 | 行号 |
|------|------|------|
| 死代码检测主入口 | `refactor.py:380-550` | `find_dead_code()` |
| 9 类跳过规则 | `refactor.py:395-499` | 各条件注释标注 |
| Plausible-caller 过滤（5 条规则） | `refactor.py:552-640` | `_is_plausible_caller()` |
| 重命名预览 | `refactor.py:73-200` | `rename_preview()` |
| 安全应用（路径越界防护 + 顺序编辑） | `refactor.py:202-350` | `apply_refactor()` |
| Dry-run 不弹 refactor_id | `refactor.py:270-285` | `apply_refactor()` dry_run 分支 |
| 跨社区 move 建议 | `refactor.py:652-720` | `suggest_refactorings()` |
| 10 分钟过期锁 | `refactor.py:55-72` | `_pending_refactors` + TTL 检查 |

**全路径：** `/Users/wangyinlong/code-review-graph/code_review_graph/refactor.py`

---

## 9 类反误报跳过规则（核心价值所在）

```python
def _should_skip_dead_code_check(node, store):
    # 1. 测试节点 / 测试文件
    if node.is_test or "test" in node.file_path.lower():
        return True

    # 2. Dunder 方法（__init__, __str__, __eq__...）
    if node.name.startswith("__") and node.name.endswith("__"):
        return True

    # 3. Entry point（框架装饰器/命名约定 → 见 execution-flows 计划书）
    if node.qualified_name in entry_point_qnames:
        return True

    # 4. 类型注解中被引用（Pydantic body, FastAPI Depends）
    if any(e.kind == "REFERENCES" for e in store.get_edges_by_target(node.qname)):
        return True

    # 5. 框架 DI 装饰的类（@Injectable, @Component, @Service）
    if any(d in _DI_DECORATORS for d in node.extra.get("decorators", [])):
        return True

    # 6. 继承自框架基类（BaseModel, DeclarativeBase, Stack, Construct）
    for e in store.get_edges_by_source(node.qname):
        if e.kind == "INHERITS" and any(
            base in e.target_qualified for base in _FRAMEWORK_BASE_CLASSES
        ):
            return True

    # 7. CDK/Pulumi 类后缀启发（没有 INHERITS 边时的兜底）
    if any(node.parent_name.endswith(s) for s in _CDK_CLASS_SUFFIXES):
        return True

    # 8. 属性装饰器（@property, @classmethod, @staticmethod, @abstractmethod）
    if any(d in _METHOD_DECORATORS for d in node.extra.get("decorators", [])):
        return True

    # 9. Mock 命名（MockX, StubX, FakeX）
    if _MOCK_NAME_RE.match(node.name):
        return True

    return False
```

### Plausible-caller 过滤（减少 bare-name 假阳性）

```python
def _is_plausible_caller(edge, node, store):
    # 规则 1: 同文件调用 → 可信
    if edge.file_path == node.file_path:
        return True
    # 规则 2: 全局唯一名 → 可信
    if store.count_nodes_by_name(node.name) == 1:
        return True
    # 规则 3: 调用文件有 IMPORTS_FROM 到目标文件（直接或 barrel re-export）
    if store.has_import_path(edge.file_path, node.file_path, max_hops=2):
        return True
    # 规则 4: __init__.py 父目录前缀匹配
    if node.file_path.rsplit("/", 1)[0] in edge.file_path:
        return True
    # 规则 5: monorepo 包别名匹配
    if _matches_package_alias(edge.file_path, node.file_path):
        return True
    return False
```

---

## Luna 实施要点

1. **分阶段实施**：先实施"无入边 = 死代码"的简单版本，再逐步加 9 类反误报规则
2. **每条规则加测试**：参考代码有详细注释说明每条规则的来源，写测试时一一覆盖
3. **dry_run 模式**：`luna dead-code --dry-run` 只展示检测结果，`--apply` 才真正删除
4. **重命名预览**：`luna rename OldName NewName --preview` 展示 unified diff，确认后 apply
5. **路径越界防护**：apply 时必须检查 `edit_path.relative_to(repo_root)`，防止跨目录写入

---

## 估算

- 简单版死代码（无入边）：50 行，3 天
- 9 类反误报规则（完整版）：400 行，2 周
- 重命名预览 + apply：300 行，1 周
- 总工作量：3-4 周（可分阶段交付）
