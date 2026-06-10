# Tree-sitter 多语言扩展 — 50+ 语言统一解析层

**目标：** 用 `tree-sitter-language-pack` 一次性引入 50+ 种语言的 grammar，建立统一的解析接口，让 Luna 的前端图谱和后端适配器都从同一个解析层取数据，不再为每种语言单独维护 grammar 包。

**参考来源：** `/Users/wangyinlong/code-review-graph/code_review_graph/parser.py`（283KB）的 `EXTENSION_TO_LANGUAGE`、`_FUNCTION_TYPES`、`_CLASS_TYPES`、`_IMPORT_TYPES`、`_CALL_TYPES` 映射表

---

## 现在的痛点

Luna 目前有两套解析：
1. **前端**（`context_graph.py` + `symbol_locator.py`）：只支持 `.js/.ts/.jsx/.tsx/.vue`，每种格式单独处理
2. **后端**（`phases/adapters/` 7 个适配器）：每个语言一个文件，重复实现 `find_enclosing_symbol` 等逻辑

问题：
- 新增一种语言要写一个新适配器（约 200-300 行），门槛高
- 各适配器对 tree-sitter 的用法不一致，难以维护
- 前端解析没有函数级别的节点（只有 file + export），图谱粒度粗
- 语言覆盖有盲区（Ruby、Rust、Kotlin、Swift 等完全不支持）

---

## 架构

```
phases/unified_parser.py
  ├── EXTENSION_TO_LANGUAGE: dict[str, str]    # 50+ 种扩展名映射
  ├── NODE_TYPE_MAP: dict[str, LanguageProfile] # 各语言的节点类型定义
  ├── class ParsedNode                          # 统一节点：kind/name/file/line/parent/params
  ├── class ParsedEdge                          # 统一边：kind/source/target/file/line
  ├── parse_file(path) → tuple[list[ParsedNode], list[ParsedEdge]]
  └── parse_bytes(source, language) → ...       # 底层，可单独测试

phases/adapters/base_adapter.py（新增）
  └── class BaseTreeSitterAdapter               # 复用 unified_parser，子类只覆盖特化逻辑
```

7 个现有后端适配器逐步迁移到继承 `BaseTreeSitterAdapter`，前端 `context_graph.py` 也改用 `unified_parser.parse_file`。

---

## 语言节点类型映射（从 code-review-graph 搬移）

每种语言定义 4 张表：

```python
# 示例：Python
_PYTHON = LanguageProfile(
    function_types={"function_definition", "decorated_definition"},
    class_types={"class_definition"},
    import_types={"import_statement", "import_from_statement"},
    call_types={"call"},
    method_resolution=".",     # ClassName.method 拼接符
)
```

覆盖优先级（前 10 种，luna 最常见）：
TypeScript / JavaScript / Python / Java / C# / Go / PHP / C++ / Vue / Ruby

---

## 文件地图

| 操作 | 路径 | 职责 |
|------|------|------|
| Create | `phases/unified_parser.py` | 统一解析层：EXTENSION_TO_LANGUAGE + LanguageProfile + parse_file |
| Create | `phases/adapters/base_adapter.py` | BaseTreeSitterAdapter，复用 unified_parser |
| Modify | `phases/adapters/csharp_adapter.py` | 继承 BaseTreeSitterAdapter，删除重复解析逻辑 |
| Modify | `phases/adapters/python_adapter.py` | 同上 |
| Modify | `phases/adapters/java_adapter.py` | 同上 |
| Modify | `phases/adapters/nodejs_adapter.py` | 同上 |
| Modify | `phases/adapters/go_adapter.py` | 同上 |
| Modify | `phases/adapters/php_adapter.py` | 同上 |
| Modify | `phases/adapters/cpp_adapter.py` | 同上 |
| Modify | `phases/context_graph.py` | `_process_js_file` / `_process_vue_file` 改用 unified_parser |
| Modify | `phases/symbol_locator.py` | `_locate_symbols_ast` 改用 unified_parser |
| Modify | `pyproject.toml` | 把 7 个语言包替换为 `tree-sitter-language-pack>=0.3.0` |
| Create | `tests/test_unified_parser.py` | 测试各语言解析结果 |

---

## Task 1：unified_parser 核心

**文件：** `phases/unified_parser.py`

- [ ] 写失败测试：
  - `test_parse_typescript_finds_function_nodes`
  - `test_parse_python_finds_class_and_method`
  - `test_parse_vue_sfc_extracts_script_section`
  - `test_extension_to_language_covers_common_exts`
  - `test_parse_unsupported_ext_returns_empty`
- [ ] 确认测试失败
- [ ] 实现 `EXTENSION_TO_LANGUAGE`（从 code-review-graph 搬移，精简到 Luna 需要的 20 种）
- [ ] 实现 `@dataclass ParsedNode(kind, name, file, line, parent_name, params, language)`
- [ ] 实现 `@dataclass ParsedEdge(kind, source, target, file, line)`
- [ ] 实现 `parse_bytes(source: bytes, language: str) -> tuple[list[ParsedNode], list[ParsedEdge]]`
  - 用 `tree-sitter-language-pack` 获取 Language 对象
  - 递归遍历 AST，按 `LanguageProfile` 识别函数/类/import/call 节点
- [ ] 实现 `parse_file(path: Path) -> tuple[list[ParsedNode], list[ParsedEdge]]`
  - 按扩展名查 `EXTENSION_TO_LANGUAGE`
  - Vue 文件：提取 `<script>` 段，按 TS 解析
  - 不支持的语言：返回 `([], [])`
- [ ] 确认测试通过
- [ ] `pytest tests/test_unified_parser.py -v` 全绿
- [ ] commit：`feat: unified_parser — EXTENSION_TO_LANGUAGE + parse_file for 20+ languages`

---

## Task 2：BaseTreeSitterAdapter

**文件：** `phases/adapters/base_adapter.py`

提取现有 7 个适配器的公共逻辑：

- [ ] 写失败测试：`test_base_adapter_find_enclosing_symbol_uses_unified_parser`
- [ ] 确认测试失败
- [ ] 实现 `class BaseTreeSitterAdapter`：
  - `find_enclosing_symbol(root_node, source, line, rel_path, is_new_file)` — 通用实现，用 `unified_parser` 的节点类型
  - `extract_file_nodes(root_node, source, rel_path)` — 通用实现
  - 子类可覆盖 `_classify_node_type(node, class_name, attrs)` 做框架特化分类
- [ ] 确认测试通过
- [ ] `pytest -q` 全绿
- [ ] commit：`feat: BaseTreeSitterAdapter — shared tree-sitter logic for all adapters`

---

## Task 3：迁移现有 7 个适配器

每个适配器继承 `BaseTreeSitterAdapter`，删除重复代码，只保留框架特化部分（如 Spring `@Controller`、ASP.NET `[Authorize]` 的识别）。

- [ ] 迁移 `csharp_adapter.py`：保留 `[Authorize]`/`HttpPost` 特化，删除通用 AST 遍历逻辑
- [ ] 迁移 `python_adapter.py`：保留 FastAPI `@app.get` 装饰器识别
- [ ] 迁移 `java_adapter.py`：保留 Spring `@RestController` 识别
- [ ] 迁移 `nodejs_adapter.py`：保留 Express `.get()` / NestJS `@Controller` 识别
- [ ] 迁移 `go_adapter.py`：保留 Gin `router.GET()` 识别
- [ ] 迁移 `php_adapter.py`：保留 Laravel `Route::` 识别
- [ ] 迁移 `cpp_adapter.py`：保留 gRPC handler 识别
- [ ] 每个迁移后：`pytest tests/adapters/test_*_adapter.py -v` 通过
- [ ] `pytest -q` 全绿
- [ ] commit：`refactor: migrate all 7 adapters to BaseTreeSitterAdapter`

---

## Task 4：迁移前端解析

**文件：** `phases/context_graph.py`、`phases/symbol_locator.py`

- [ ] `context_graph.py`：`_process_js_file` 和 `_process_vue_file` 改用 `unified_parser.parse_file`，从 `ParsedNode`/`ParsedEdge` 构建 `GraphNode`/`GraphEdge`
- [ ] `symbol_locator.py`：`_locate_symbols_ast` 改用 `unified_parser.parse_file`，从 `ParsedNode` 转 `ChangedSymbol`
- [ ] 写测试：`test_context_graph_uses_unified_parser`、`test_symbol_locator_uses_unified_parser`
- [ ] 确认测试通过
- [ ] `pytest -q` 全绿
- [ ] commit：`refactor: context_graph and symbol_locator use unified_parser`

---

## Task 5：pyproject.toml 依赖简化

把 7 个语言 grammar 包替换为 `tree-sitter-language-pack`：

- [ ] 检查 `tree-sitter-language-pack` 是否包含 Luna 需要的全部语言
- [ ] 修改 `pyproject.toml`：删除 `tree-sitter-c-sharp`、`tree-sitter-java` 等 7 个包，加 `tree-sitter-language-pack>=0.3.0`
- [ ] `pip3 install -e ".[dev]"`
- [ ] `pytest -q` 全绿
- [ ] commit：`chore: replace 7 grammar packages with tree-sitter-language-pack`

---

## Task 6：新增语言（Ruby / Rust / Kotlin）

利用统一层，零成本扩展到新语言：

- [ ] 在 `unified_parser.py` 的 `EXTENSION_TO_LANGUAGE` 和 `NODE_TYPE_MAP` 中添加：
  - Ruby（`.rb`）：`method`、`class`
  - Rust（`.rs`）：`fn_item`、`impl_item`
  - Kotlin（`.kt`）：`function_declaration`、`class_declaration`
- [ ] 写测试：`test_parse_ruby_finds_methods`、`test_parse_rust_finds_functions`
- [ ] 确认测试通过
- [ ] commit：`feat: unified_parser supports Ruby, Rust, Kotlin`

---

## Task 7：验证

```bash
pytest -q
```

手动验证：
```bash
# 在含 Ruby/Rust 项目目录
luna --staged
```

验收标准：
- 现有 7 种语言测试全绿（无回归）
- Ruby/Rust/Kotlin 基础节点可正确提取
- `pyproject.toml` 依赖列表更简洁
- 各适配器代码量减少约 30-40%（删除重复逻辑）

---

## Non-Goals（本阶段不做）

- 50 种语言全部支持（先做最常见的 10-15 种）
- 语言特化的框架识别扩展（Spring DI、Temporal 等，留给后续迭代）
- `.ipynb` Jupyter Notebook 支持
