# Tree-sitter Backend Graph Engine — Plan A: Foundation + C# Adapter

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace regex-based C# analysis with a unified tree-sitter engine + LanguageAdapter protocol, establishing the architecture that Plans B and C will extend to Java, Python, Go, Node.js, PHP, and C++.

**Architecture:** A language-agnostic `backend_graph_engine.py` handles file scanning, AST parsing, graph building, and caching. A `LanguageAdapter` Protocol defines the contract. `phases/adapters/csharp_adapter.py` implements C# support using tree-sitter-c-sharp for precise AST-based symbol extraction and call edge resolution. The three deprecated regex modules (`csharp_context_graph.py`, `csharp_symbol_locator.py`, `backend_generic_symbol_locator.py`) are deleted after their tests are migrated.

**Tech Stack:** Python 3.11+, tree-sitter>=0.23, tree-sitter-c-sharp>=0.23, pytest

---

## File Map

| Action | Path | Responsibility |
|--------|------|----------------|
| Modify | `pyproject.toml` | Add tree-sitter deps; add `phases.adapters` package |
| Create | `phases/backend_language_adapter.py` | `LanguageAdapter` Protocol — contract every adapter must satisfy |
| Create | `phases/backend_graph_engine.py` | Engine: file scan, parse, build graph, cache, find symbol |
| Create | `phases/adapters/__init__.py` | Adapter registry: `get_adapter(name) → LanguageAdapter` |
| Create | `phases/adapters/csharp_adapter.py` | C# tree-sitter adapter — symbol extraction, call edges, auth edges |
| Create | `tests/adapters/__init__.py` | Empty — makes pytest discover tests/adapters/ |
| Create | `tests/adapters/test_csharp_adapter.py` | C# adapter unit tests |
| Create | `tests/test_backend_graph_engine.py` | Engine unit tests |
| Modify | `phases/backend_adapter_registry.py` | Add `get_adapter(lang) → LanguageAdapter` |
| Modify | `luna.py` | Use engine API instead of `build_csharp_backend_graph` |
| Replace | `tests/test_csharp_context_graph.py` | Re-target to engine + C# adapter API |
| Replace | `tests/test_csharp_symbol_locator.py` | Re-target to engine `find_symbols_from_diff` API |
| Delete | `phases/csharp_context_graph.py` | Superseded by engine + csharp_adapter |
| Delete | `phases/csharp_symbol_locator.py` | Superseded by engine + csharp_adapter |
| Delete | `phases/backend_generic_symbol_locator.py` | Superseded by engine + adapters |

---

## Task 1: Install tree-sitter and update pyproject.toml

**Files:**
- Modify: `pyproject.toml`

- [ ] **Step 1: Update `pyproject.toml`**

Replace the `[project]` dependencies and `[tool.setuptools]` sections with:

```toml
[project]
name = "luna"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = [
    "click>=8.1",
    "anthropic>=0.28",
    "pyyaml>=6.0",
    "openai>=1.0",
    "tree-sitter>=0.23",
    "tree-sitter-c-sharp>=0.23",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.0",
    "pytest-mock>=3.12",
]

[tool.setuptools]
py-modules = ["luna", "config", "diff_reader", "skill_loader", "confirmer", "api_client", "reporter", "test_importer"]
packages = ["phases", "phases.adapters"]
```

- [ ] **Step 2: Install new dependencies**

```bash
pip3 install -e ".[dev]"
```

Expected: tree-sitter and tree-sitter-c-sharp install without errors.

- [ ] **Step 3: Verify tree-sitter imports work**

```bash
python3 -c "import tree_sitter_c_sharp; from tree_sitter import Language, Parser; print('OK')"
```

Expected: `OK`

- [ ] **Step 4: Run existing tests to confirm no breakage**

```bash
pytest -q
```

Expected: 106 passed (no new failures from dependency changes)

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml
git commit -m "chore: add tree-sitter deps and phases.adapters package"
```

---

## Task 2: LanguageAdapter Protocol

**Files:**
- Create: `phases/backend_language_adapter.py`

- [ ] **Step 1: Write failing import test**

```python
# tests/test_backend_graph_engine.py  (create this file)
from phases.backend_language_adapter import LanguageAdapter


def test_language_adapter_protocol_is_importable():
    assert LanguageAdapter is not None
```

- [ ] **Step 2: Run test to confirm it fails**

```bash
pytest tests/test_backend_graph_engine.py -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'phases.backend_language_adapter'`

- [ ] **Step 3: Create `phases/backend_language_adapter.py`**

```python
# phases/backend_language_adapter.py
from __future__ import annotations
from typing import Any, Protocol, runtime_checkable

from phases.backend_models import BackendChangedSymbol, BackendGraphEdge, BackendGraphNode


@runtime_checkable
class LanguageAdapter(Protocol):
    """Contract every backend language adapter must satisfy.

    The engine calls these methods; adapters provide language-specific
    tree-sitter logic. Adapters must not contain any file-scanning,
    caching, or graph-assembly logic — that belongs in the engine.
    """

    name: str                    # "csharp", "python", "java", …
    extensions: tuple[str, ...]  # (".cs",), (".py",), …

    def get_language(self) -> Any:
        """Return the tree-sitter Language object for this language (lazy-loaded)."""
        ...

    def find_enclosing_symbol(
        self,
        root_node: Any,
        source: bytes,
        line: int,           # 1-based line number from git diff hunk
        rel_path: str,
        is_new_file: bool,
    ) -> BackendChangedSymbol | None:
        """Given a changed line, return the symbol (function/method/property) that contains it.

        Uses tree-sitter's node.parent chain to walk upward from the node
        at `line` until a method/function/property declaration is found.
        Returns None if no enclosing symbol is found (e.g., top-level import).
        """
        ...

    def extract_file_nodes(
        self,
        root_node: Any,
        source: bytes,
        rel_path: str,
    ) -> list[BackendGraphNode]:
        """Extract all symbol nodes from a source file (full project scan).

        Returns one BackendGraphNode per function/method/property/handler.
        Node IDs must follow the format: "{rel_path}:{ClassName}.{MethodName}"
        """
        ...

    def extract_file_edges(
        self,
        root_node: Any,
        source: bytes,
        rel_path: str,
        method_index: dict[str, str],  # "ClassName.Method" → node_id
    ) -> list[BackendGraphEdge]:
        """Extract all relationship edges from a source file.

        Edge types include: "calls", "requires_auth", "writes_db",
        "exposes_endpoint", "calls_external_api".
        Use method_index to resolve call targets by name.
        """
        ...
```

- [ ] **Step 4: Run test to confirm it passes**

```bash
pytest tests/test_backend_graph_engine.py -v
```

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add phases/backend_language_adapter.py tests/test_backend_graph_engine.py
git commit -m "feat: add LanguageAdapter protocol"
```

---

## Task 3: Backend Graph Engine

**Files:**
- Create: `phases/backend_graph_engine.py`
- Modify: `tests/test_backend_graph_engine.py`

- [ ] **Step 1: Add engine tests**

Append to `tests/test_backend_graph_engine.py`:

```python
import json
from pathlib import Path
from unittest.mock import MagicMock
from phases.backend_graph_engine import build_graph, find_symbols_from_diff, save_graph, load_graph
from phases.backend_models import BackendChangedSymbol, BackendContextGraph, BackendGraphEdge, BackendGraphNode


def _make_mock_adapter(ext=".cs"):
    """Minimal mock adapter for engine unit tests."""
    adapter = MagicMock()
    adapter.name = "mock"
    adapter.extensions = (ext,)
    adapter.get_language.return_value = None  # engine will not call tree-sitter with None language
    adapter.extract_file_nodes.return_value = [
        BackendGraphNode(id="foo.cs:Foo.Bar", node_type="method", file="foo.cs", name="Foo.Bar", line=1)
    ]
    adapter.extract_file_edges.return_value = []
    adapter.find_enclosing_symbol.return_value = BackendChangedSymbol(
        file="foo.cs", symbol="Bar", symbol_type="method",
        class_name="Foo", start_line=1, change_type="modified",
    )
    return adapter


def test_build_graph_calls_adapter_per_file(tmp_path):
    (tmp_path / "foo.cs").write_text("class Foo { void Bar() {} }", encoding="utf-8")
    adapter = _make_mock_adapter()

    # Patch tree-sitter parse inside engine to avoid real parsing
    import phases.backend_graph_engine as eng
    original_parse = eng._parse_file
    eng._parse_file = lambda path, adapter: (MagicMock(), path.read_bytes())
    try:
        graph = build_graph(adapter, project_root=str(tmp_path))
    finally:
        eng._parse_file = original_parse

    assert "foo.cs:Foo.Bar" in graph.nodes
    adapter.extract_file_nodes.assert_called_once()


def test_save_and_load_graph_roundtrip(tmp_path):
    graph = BackendContextGraph()
    graph.add_node(BackendGraphNode(id="a.cs:A.B", node_type="method", file="a.cs", name="A.B", line=1))
    graph.add_edge(BackendGraphEdge(source="a.cs:A.B", target="b.cs:C.D", edge_type="calls", evidence="line 5", confidence="medium"))
    cache = tmp_path / "g.json"
    save_graph(graph, str(cache))

    loaded = load_graph(str(cache))
    assert loaded is not None
    assert "a.cs:A.B" in loaded.nodes
    assert loaded.edges[0].edge_type == "calls"


def test_load_graph_returns_none_for_missing():
    assert load_graph("/nonexistent/graph.json") is None


def test_find_symbols_from_diff_uses_adapter(tmp_path):
    (tmp_path / "Controllers" / "Foo.cs").mkdir(parents=True)
    (tmp_path / "Controllers" / "Foo.cs").rmdir()
    cs = tmp_path / "Controllers" / "Foo.cs"
    cs.parent.mkdir(parents=True, exist_ok=True)
    cs.write_text("class Foo { void Bar() {} }", encoding="utf-8")

    diff = """\
diff --git a/Controllers/Foo.cs b/Controllers/Foo.cs
--- a/Controllers/Foo.cs
+++ b/Controllers/Foo.cs
@@ -1,1 +1,2 @@
+    // changed
"""
    adapter = _make_mock_adapter()
    import phases.backend_graph_engine as eng
    original_parse = eng._parse_file
    eng._parse_file = lambda path, adapter: (MagicMock(), path.read_bytes())
    try:
        symbols = find_symbols_from_diff(diff, adapter, project_root=str(tmp_path))
    finally:
        eng._parse_file = original_parse

    assert len(symbols) == 1
    assert symbols[0].symbol == "Bar"
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
pytest tests/test_backend_graph_engine.py -v
```

Expected: FAIL — `cannot import name 'build_graph' from 'phases.backend_graph_engine'`

- [ ] **Step 3: Create `phases/backend_graph_engine.py`**

```python
# phases/backend_graph_engine.py
from __future__ import annotations
import json
from pathlib import Path
from typing import Any

from phases.backend_language_adapter import LanguageAdapter
from phases.backend_models import (
    BackendChangedSymbol,
    BackendContextGraph,
    BackendGraphEdge,
    BackendGraphNode,
)
from phases.symbol_locator import parse_diff

_SKIP_DIRS = {"bin", "obj", ".git", ".vs", "node_modules", "dist", "build", "__pycache__", ".luna"}


def find_symbols_from_diff(
    diff: str,
    adapter: LanguageAdapter,
    project_root: str = ".",
) -> list[BackendChangedSymbol]:
    """Extract changed symbols from a git diff using the adapter's AST locator."""
    root = Path(project_root)
    symbols: list[BackendChangedSymbol] = []
    seen: set[str] = set()

    for diff_file in parse_diff(diff):
        if not any(diff_file.path.endswith(ext) for ext in adapter.extensions):
            continue
        if diff_file.is_deleted:
            continue
        abs_path = root / diff_file.path
        if not abs_path.exists():
            continue

        root_node, source = _parse_file(abs_path, adapter)

        changed_lines = [
            ln
            for hunk in diff_file.hunks
            for ln in range(hunk.start_line, hunk.start_line + hunk.line_count)
        ]

        for line_no in changed_lines:
            symbol = adapter.find_enclosing_symbol(
                root_node, source, line_no, diff_file.path, diff_file.is_new_file
            )
            if symbol and symbol.node_id not in seen:
                seen.add(symbol.node_id)
                symbols.append(symbol)

    return symbols


def build_graph(
    adapter: LanguageAdapter,
    project_root: str = ".",
) -> BackendContextGraph:
    """Build a BackendContextGraph by scanning all files matching adapter.extensions."""
    root = Path(project_root)
    graph = BackendContextGraph()
    method_index: dict[str, str] = {}

    files = [
        p for p in root.rglob("*")
        if p.is_file()
        and any(p.suffix == ext for ext in adapter.extensions)
        and not any(part in _SKIP_DIRS for part in p.relative_to(root).parts)
    ]

    # First pass: build nodes and method index
    for path in files:
        rel = str(path.relative_to(root))
        try:
            root_node, source = _parse_file(path, adapter)
        except OSError:
            continue
        nodes = adapter.extract_file_nodes(root_node, source, rel)
        for node in nodes:
            graph.add_node(node)
            method_index[node.name] = node.id
            short = node.name.split(".")[-1]
            method_index.setdefault(short, node.id)

    # Second pass: build edges
    for path in files:
        rel = str(path.relative_to(root))
        try:
            root_node, source = _parse_file(path, adapter)
        except OSError:
            continue
        edges = adapter.extract_file_edges(root_node, source, rel, method_index)
        for edge in edges:
            graph.add_edge(edge)

    return graph


def save_graph(graph: BackendContextGraph, path: str) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    data = {
        "nodes": {
            nid: {
                "id": n.id, "node_type": n.node_type, "file": n.file,
                "name": n.name, "line": n.line, "attributes": n.attributes,
            }
            for nid, n in graph.nodes.items()
        },
        "edges": [
            {
                "source": e.source, "target": e.target, "edge_type": e.edge_type,
                "evidence": e.evidence, "confidence": e.confidence,
            }
            for e in graph.edges
        ],
    }
    Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def load_graph(path: str) -> BackendContextGraph | None:
    p = Path(path)
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    graph = BackendContextGraph()
    for nid, n in data.get("nodes", {}).items():
        graph.add_node(BackendGraphNode(
            id=n["id"], node_type=n["node_type"], file=n["file"],
            name=n["name"], line=n.get("line", 0), attributes=n.get("attributes", []),
        ))
    for e in data.get("edges", []):
        graph.add_edge(BackendGraphEdge(
            source=e["source"], target=e["target"], edge_type=e["edge_type"],
            evidence=e["evidence"], confidence=e.get("confidence", "high"),
        ))
    return graph


def _parse_file(path: Path, adapter: LanguageAdapter) -> tuple[Any, bytes]:
    """Parse a source file and return (root_node, source_bytes)."""
    from tree_sitter import Language, Parser
    source = path.read_bytes()
    lang = Language(adapter.get_language())
    parser = Parser(lang)
    tree = parser.parse(source)
    return tree.root_node, source
```

- [ ] **Step 4: Run engine tests**

```bash
pytest tests/test_backend_graph_engine.py -v
```

Expected: all PASS

- [ ] **Step 5: Run full suite**

```bash
pytest -q
```

Expected: all pass (106 + new engine tests)

- [ ] **Step 6: Commit**

```bash
git add phases/backend_graph_engine.py tests/test_backend_graph_engine.py
git commit -m "feat: add backend graph engine"
```

---

## Task 4: C# Adapter

The C# adapter uses tree-sitter-c-sharp to precisely parse method declarations, attribute lists, property declarations, and call expressions. It replaces the regex logic in `csharp_context_graph.py` and `csharp_symbol_locator.py`.

**Files:**
- Create: `phases/adapters/__init__.py`
- Create: `phases/adapters/csharp_adapter.py`
- Create: `tests/adapters/__init__.py`
- Create: `tests/adapters/test_csharp_adapter.py`

- [ ] **Step 1: Discover tree-sitter-c-sharp node types**

Before writing the adapter, run this script to confirm the exact node type names in the installed version of tree-sitter-c-sharp:

```python
# Save as /tmp/probe_csharp.py and run: python3 /tmp/probe_csharp.py
import tree_sitter_c_sharp as tscsharp
from tree_sitter import Language, Parser

CS = Language(tscsharp.language())
parser = Parser(CS)

source = b"""\
using System;
namespace Demo {
    public class OrderController {
        [Authorize]
        [HttpPost("submit")]
        public IActionResult Submit(SubmitOrderRequest request) {
            return Ok(_orderService.Submit(request));
        }
        public string Amount { get; set; }
    }
}
"""
tree = parser.parse(source)

def show(node, depth=0):
    if depth > 8:
        return
    name = f"[{node.type}]"
    if not node.children:
        name += f" = {node.text!r}"
    print("  " * depth + name)
    for child in node.children:
        show(child, depth + 1)

show(tree.root_node)
```

Run it:
```bash
python3 /tmp/probe_csharp.py 2>&1 | head -80
```

Note the exact node type names for:
- method declaration (likely `method_declaration`)
- class declaration (likely `class_declaration`)
- attribute list (likely `attribute_list`)
- attribute (likely `attribute`)
- property declaration (likely `property_declaration`)
- field with `private readonly` (likely `field_declaration`)
- invocation / call (likely `invocation_expression`)

- [ ] **Step 2: Write failing adapter tests**

```python
# tests/adapters/__init__.py  (empty file)
```

```python
# tests/adapters/test_csharp_adapter.py
from pathlib import Path
import tree_sitter_c_sharp as tscsharp
from tree_sitter import Language, Parser
from phases.adapters.csharp_adapter import CSharpAdapter
from phases.backend_models import BackendGraphEdge

ADAPTER = CSharpAdapter()

CS = Language(tscsharp.language())
_parser = Parser(CS)


def _parse(src: str):
    source = src.encode("utf-8")
    tree = _parser.parse(source)
    return tree.root_node, source


CONTROLLER_SRC = """\
public class OrderController : ControllerBase
{
    private readonly OrderService _orderService;

    [Authorize]
    [HttpPost("submit")]
    public IActionResult Submit(SubmitOrderRequest request)
    {
        if (request.Amount <= 0) return BadRequest();
        return Ok(_orderService.Submit(request));
    }
}
"""

MODEL_SRC = """\
public class SubmitOrderRequest
{
    public decimal Amount { get; set; }
}
"""


def test_adapter_name_and_extensions():
    assert ADAPTER.name == "csharp"
    assert ".cs" in ADAPTER.extensions


def test_extract_file_nodes_finds_controller_method():
    root, src = _parse(CONTROLLER_SRC)
    nodes = ADAPTER.extract_file_nodes(root, src, "Controllers/OrderController.cs")
    node_ids = [n.id for n in nodes]
    assert "Controllers/OrderController.cs:OrderController.Submit" in node_ids


def test_extract_file_nodes_finds_model_property():
    root, src = _parse(MODEL_SRC)
    nodes = ADAPTER.extract_file_nodes(root, src, "Models/SubmitOrderRequest.cs")
    node_ids = [n.id for n in nodes]
    assert "Models/SubmitOrderRequest.cs:SubmitOrderRequest.Amount" in node_ids


def test_extract_file_nodes_controller_action_type():
    root, src = _parse(CONTROLLER_SRC)
    nodes = ADAPTER.extract_file_nodes(root, src, "Controllers/OrderController.cs")
    submit = next(n for n in nodes if n.name == "OrderController.Submit")
    assert submit.node_type == "controller_action"


def test_extract_file_nodes_model_property_type():
    root, src = _parse(MODEL_SRC)
    nodes = ADAPTER.extract_file_nodes(root, src, "Models/SubmitOrderRequest.cs")
    amount = next(n for n in nodes if n.name == "SubmitOrderRequest.Amount")
    assert amount.node_type == "model_property"


def test_extract_file_nodes_collects_attributes():
    root, src = _parse(CONTROLLER_SRC)
    nodes = ADAPTER.extract_file_nodes(root, src, "Controllers/OrderController.cs")
    submit = next(n for n in nodes if n.name == "OrderController.Submit")
    assert "Authorize" in submit.attributes
    assert "HttpPost" in submit.attributes


def test_extract_file_edges_requires_auth():
    root, src = _parse(CONTROLLER_SRC)
    nodes = ADAPTER.extract_file_nodes(root, src, "Controllers/OrderController.cs")
    method_index = {n.name: n.id for n in nodes}
    edges = ADAPTER.extract_file_edges(root, src, "Controllers/OrderController.cs", method_index)
    assert any(e.edge_type == "requires_auth" for e in edges)


def test_extract_file_edges_exposes_endpoint():
    root, src = _parse(CONTROLLER_SRC)
    nodes = ADAPTER.extract_file_nodes(root, src, "Controllers/OrderController.cs")
    method_index = {n.name: n.id for n in nodes}
    edges = ADAPTER.extract_file_edges(root, src, "Controllers/OrderController.cs", method_index)
    assert any(e.edge_type == "exposes_endpoint" for e in edges)


SERVICE_SRC = """\
public class OrderService
{
    public OrderResult Submit(SubmitOrderRequest request)
    {
        return new OrderResult();
    }
}
"""


def test_extract_file_edges_calls_edge():
    """Controller calling a service method via injected field produces a calls edge."""
    ctrl_root, ctrl_src = _parse(CONTROLLER_SRC)
    svc_root, svc_src = _parse(SERVICE_SRC)

    ctrl_nodes = ADAPTER.extract_file_nodes(ctrl_root, ctrl_src, "Controllers/OrderController.cs")
    svc_nodes = ADAPTER.extract_file_nodes(svc_root, svc_src, "Services/OrderService.cs")
    method_index = {n.name: n.id for n in ctrl_nodes + svc_nodes}

    edges = ADAPTER.extract_file_edges(ctrl_root, ctrl_src, "Controllers/OrderController.cs", method_index)
    call_edges = [e for e in edges if e.edge_type == "calls"]
    assert any(
        e.source == "Controllers/OrderController.cs:OrderController.Submit"
        and e.target == "Services/OrderService.cs:OrderService.Submit"
        for e in call_edges
    )


def test_find_enclosing_symbol_modified_line():
    """A change inside method body returns the enclosing method symbol."""
    root, src = _parse(CONTROLLER_SRC)
    # Line 9 is inside Submit method body ("if (request.Amount <= 0)...")
    symbol = ADAPTER.find_enclosing_symbol(root, src, 9, "Controllers/OrderController.cs", False)
    assert symbol is not None
    assert symbol.symbol == "Submit"
    assert symbol.class_name == "OrderController"
    assert symbol.symbol_type == "controller_action"
    assert "Authorize" in symbol.attributes


def test_find_enclosing_symbol_returns_none_for_namespace():
    """A change on a using/import line returns None."""
    src = "using System;\nnamespace Foo {}"
    root, source = _parse(src)
    symbol = ADAPTER.find_enclosing_symbol(root, source, 1, "Foo.cs", False)
    assert symbol is None
```

- [ ] **Step 3: Run tests to confirm they fail**

```bash
pytest tests/adapters/test_csharp_adapter.py -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'phases.adapters'`

- [ ] **Step 4: Create `phases/adapters/__init__.py`**

```python
# phases/adapters/__init__.py
from __future__ import annotations
from phases.backend_language_adapter import LanguageAdapter


def get_adapter(language: str) -> LanguageAdapter:
    """Return the adapter instance for the given language name."""
    language = language.lower().replace("node.js", "nodejs").replace("c++", "cpp")
    if language == "csharp":
        from phases.adapters.csharp_adapter import CSHARP_ADAPTER
        return CSHARP_ADAPTER
    raise ValueError(f"No adapter registered for language: {language!r}")
```

- [ ] **Step 5: Create `phases/adapters/csharp_adapter.py`**

```python
# phases/adapters/csharp_adapter.py
from __future__ import annotations
import re
from typing import Any

from phases.backend_models import BackendChangedSymbol, BackendGraphEdge, BackendGraphNode


class CSharpAdapter:
    name = "csharp"
    extensions = (".cs",)

    def get_language(self) -> Any:
        import tree_sitter_c_sharp as tscsharp
        return tscsharp.language()

    # ------------------------------------------------------------------ #
    # Diff analysis                                                         #
    # ------------------------------------------------------------------ #

    def find_enclosing_symbol(
        self,
        root_node: Any,
        source: bytes,
        line: int,
        rel_path: str,
        is_new_file: bool,
    ) -> BackendChangedSymbol | None:
        target_line = line - 1  # tree-sitter is 0-based

        # Walk down to the smallest node at the target line
        point = (target_line, 0)
        leaf = root_node.descendant_for_point_range(point, point)
        if leaf is None:
            return None

        # Walk up to find enclosing method or property
        enclosing = None
        node = leaf
        while node is not None:
            if node.type in ("method_declaration", "property_declaration", "constructor_declaration"):
                enclosing = node
                break
            node = node.parent

        if enclosing is None:
            return None

        class_name = _enclosing_class_name(enclosing, source)
        if not class_name:
            return None

        name_node = enclosing.child_by_field_name("name")
        if name_node is None:
            return None
        method_name = _text(name_node, source)

        attrs = _collect_attributes(enclosing, source)
        sym_type = (
            _classify_method(class_name, attrs)
            if enclosing.type != "property_declaration"
            else _classify_property(rel_path, class_name)
        )

        return BackendChangedSymbol(
            file=rel_path,
            symbol=method_name,
            symbol_type=sym_type,
            class_name=class_name,
            start_line=enclosing.start_point[0] + 1,
            change_type="added" if is_new_file else "modified",
            attributes=attrs,
            evidence=f"{rel_path}:{enclosing.start_point[0] + 1} {_first_line(enclosing, source)}",
        )

    # ------------------------------------------------------------------ #
    # Graph building                                                        #
    # ------------------------------------------------------------------ #

    def extract_file_nodes(
        self,
        root_node: Any,
        source: bytes,
        rel_path: str,
    ) -> list[BackendGraphNode]:
        nodes: list[BackendGraphNode] = []
        for class_node in _find_all(root_node, "class_declaration"):
            class_name_node = class_node.child_by_field_name("name")
            if class_name_node is None:
                continue
            class_name = _text(class_name_node, source)

            for method_node in _find_all(class_node, "method_declaration"):
                name_node = method_node.child_by_field_name("name")
                if name_node is None:
                    continue
                method_name = _text(name_node, source)
                attrs = _collect_attributes(method_node, source)
                node_id = f"{rel_path}:{class_name}.{method_name}"
                nodes.append(BackendGraphNode(
                    id=node_id,
                    node_type=_classify_method(class_name, attrs),
                    file=rel_path,
                    name=f"{class_name}.{method_name}",
                    line=method_node.start_point[0] + 1,
                    attributes=attrs,
                ))

            for prop_node in _find_all(class_node, "property_declaration"):
                name_node = prop_node.child_by_field_name("name")
                if name_node is None:
                    continue
                prop_name = _text(name_node, source)
                node_id = f"{rel_path}:{class_name}.{prop_name}"
                nodes.append(BackendGraphNode(
                    id=node_id,
                    node_type=_classify_property(rel_path, class_name),
                    file=rel_path,
                    name=f"{class_name}.{prop_name}",
                    line=prop_node.start_point[0] + 1,
                ))

        return nodes

    def extract_file_edges(
        self,
        root_node: Any,
        source: bytes,
        rel_path: str,
        method_index: dict[str, str],
    ) -> list[BackendGraphEdge]:
        edges: list[BackendGraphEdge] = []

        for class_node in _find_all(root_node, "class_declaration"):
            class_name_node = class_node.child_by_field_name("name")
            if class_name_node is None:
                continue
            class_name = _text(class_name_node, source)

            # Collect injected field types: private readonly OrderService _orderService
            field_types: dict[str, str] = {}
            for field_node in _find_all(class_node, "field_declaration"):
                field_src = source[field_node.start_byte:field_node.end_byte].decode("utf-8", errors="ignore")
                m = re.search(r"private\s+readonly\s+(\w+)\s+(_\w+)", field_src)
                if m:
                    field_types[m.group(2)] = m.group(1)

            for method_node in _find_all(class_node, "method_declaration"):
                name_node = method_node.child_by_field_name("name")
                if name_node is None:
                    continue
                method_name = _text(name_node, source)
                source_id = f"{rel_path}:{class_name}.{method_name}"
                attrs = _collect_attributes(method_node, source)
                method_src = source[method_node.start_byte:method_node.end_byte].decode("utf-8", errors="ignore")
                method_start_line = method_node.start_point[0] + 1

                # [Authorize] → requires_auth edge
                if "Authorize" in attrs:
                    edges.append(BackendGraphEdge(
                        source=source_id,
                        target=f"auth:{class_name}.{method_name}",
                        edge_type="requires_auth",
                        evidence=f"{rel_path}:{method_start_line} [Authorize]",
                        confidence="high",
                    ))

                # [HttpGet/Post/Put/Delete/…] → exposes_endpoint edge
                for attr in attrs:
                    if attr.startswith("Http"):
                        edges.append(BackendGraphEdge(
                            source=source_id,
                            target=f"endpoint:{class_name}.{method_name}",
                            edge_type="exposes_endpoint",
                            evidence=f"{rel_path}:{method_start_line} [{attr}]",
                            confidence="high",
                        ))
                        break  # one endpoint edge per method

                # _field.Method() → calls edge
                for field_name, type_name in field_types.items():
                    pattern = rf"\b{re.escape(field_name)}\.(\w+)\s*\("
                    for call_m in re.finditer(pattern, method_src):
                        called = call_m.group(1)
                        target_id = method_index.get(f"{type_name}.{called}") or method_index.get(called)
                        if target_id and target_id != source_id:
                            line_offset = method_src[: call_m.start()].count("\n")
                            edges.append(BackendGraphEdge(
                                source=source_id,
                                target=target_id,
                                edge_type="calls",
                                evidence=f"{rel_path}:{method_start_line + line_offset} {call_m.group(0)}",
                                confidence="medium",
                            ))

                # SaveChanges/SaveChangesAsync → writes_db edge
                for db_m in re.finditer(r"SaveChanges(?:Async)?\s*\(", method_src):
                    line_offset = method_src[: db_m.start()].count("\n")
                    edges.append(BackendGraphEdge(
                        source=source_id,
                        target=f"db:{rel_path}:{method_start_line + line_offset}",
                        edge_type="writes_db",
                        evidence=f"{rel_path}:{method_start_line + line_offset} {db_m.group(0)}",
                        confidence="high",
                    ))

                # HttpClient calls → calls_external_api edge
                for ext_m in re.finditer(r"(?:GetAsync|PostAsync|PutAsync|DeleteAsync|SendAsync)\s*\(", method_src):
                    line_offset = method_src[: ext_m.start()].count("\n")
                    edges.append(BackendGraphEdge(
                        source=source_id,
                        target=f"external:{rel_path}:{method_start_line + line_offset}",
                        edge_type="calls_external_api",
                        evidence=f"{rel_path}:{method_start_line + line_offset} {ext_m.group(0)}",
                        confidence="medium",
                    ))

        return edges


# ------------------------------------------------------------------ #
# Private helpers                                                       #
# ------------------------------------------------------------------ #

def _text(node: Any, source: bytes) -> str:
    if node is None:
        return ""
    return source[node.start_byte:node.end_byte].decode("utf-8", errors="ignore").strip()


def _first_line(node: Any, source: bytes) -> str:
    return _text(node, source).split("\n")[0].strip()


def _find_all(node: Any, node_type: str) -> list:
    results = []
    if node.type == node_type:
        results.append(node)
    for child in node.children:
        results.extend(_find_all(child, node_type))
    return results


def _collect_attributes(method_node: Any, source: bytes) -> list[str]:
    """Collect attribute names from attribute_list children of the method node."""
    attrs: list[str] = []
    for child in method_node.children:
        if child.type == "attribute_list":
            for grandchild in child.children:
                if grandchild.type == "attribute":
                    name_node = grandchild.child_by_field_name("name")
                    if name_node:
                        # Strip arguments: "HttpPost" from "HttpPost(\"submit\")"
                        attrs.append(_text(name_node, source).split("(")[0])
    return attrs


def _enclosing_class_name(node: Any, source: bytes) -> str:
    """Walk up the parent chain to find the enclosing class_declaration name."""
    current = node.parent
    while current is not None:
        if current.type == "class_declaration":
            name_node = current.child_by_field_name("name")
            if name_node:
                return _text(name_node, source)
        current = current.parent
    return ""


def _classify_method(class_name: str, attributes: list[str]) -> str:
    if class_name.endswith("Controller") or any(a.startswith("Http") for a in attributes):
        return "controller_action"
    if class_name.endswith("Service"):
        return "service_method"
    if class_name.endswith("Repository"):
        return "repository_method"
    return "method"


def _classify_property(rel_path: str, class_name: str) -> str:
    lower = f"{rel_path} {class_name}".lower()
    if "entity" in lower:
        return "entity_property"
    if any(t in lower for t in ("model", "dto", "request", "response")):
        return "model_property"
    return "property"


CSHARP_ADAPTER = CSharpAdapter()
```

- [ ] **Step 6: Run adapter tests**

```bash
pytest tests/adapters/test_csharp_adapter.py -v
```

Expected: all 11 tests pass.

If any attribute-related test fails (e.g., `test_extract_file_nodes_collects_attributes`), run the probe script from Step 1 and check whether `attribute_list` is a direct child of `method_declaration` or a sibling. Adjust `_collect_attributes` accordingly:

**If attribute_list is a sibling** (appears before method_declaration in parent.children):
```python
def _collect_attributes(method_node: Any, source: bytes) -> list[str]:
    parent = method_node.parent
    if parent is None:
        return []
    attrs: list[str] = []
    for child in parent.children:
        if child is method_node:
            break
        if child.type == "attribute_list":
            for grandchild in child.children:
                if grandchild.type == "attribute":
                    name_node = grandchild.child_by_field_name("name")
                    if name_node:
                        attrs.append(_text(name_node, source).split("(")[0])
        elif child.is_named and child.type not in ("attribute_list",):
            attrs = []  # reset on non-attribute named node
    return attrs
```

- [ ] **Step 7: Run full suite**

```bash
pytest -q
```

Expected: all pass

- [ ] **Step 8: Commit**

```bash
git add phases/adapters/__init__.py phases/adapters/csharp_adapter.py tests/adapters/__init__.py tests/adapters/test_csharp_adapter.py
git commit -m "feat: add C# tree-sitter adapter with AST-based symbol extraction"
```

---

## Task 5: Update Adapter Registry

**Files:**
- Modify: `phases/backend_adapter_registry.py`

- [ ] **Step 1: Read current file**

```bash
cat phases/backend_adapter_registry.py
```

- [ ] **Step 2: Add `get_adapter` function**

Append to the end of `phases/backend_adapter_registry.py`:

```python
def get_adapter(language: str):
    """Return the LanguageAdapter instance for the given language name.

    Raises ValueError if no adapter is registered for that language.
    Plans B and C add adapters for java, python, nodejs, go, php, cpp.
    """
    from phases.adapters import get_adapter as _get
    return _get(language)
```

- [ ] **Step 3: Run tests to confirm no regressions**

```bash
pytest -q
```

Expected: all pass

- [ ] **Step 4: Commit**

```bash
git add phases/backend_adapter_registry.py
git commit -m "feat: expose get_adapter via backend_adapter_registry"
```

---

## Task 6: Update luna.py to use the engine

**Files:**
- Modify: `luna.py`

Replace the backend analysis block in `luna.py`.

- [ ] **Step 1: Read current backend block in luna.py**

```bash
grep -n "backend_graph\|build_csharp\|extract_csharp\|BackendContextGraph\|backend_generic" luna.py
```

- [ ] **Step 2: Replace backend imports at top of luna.py**

Find and replace these import lines:

Old:
```python
from phases.csharp_symbol_locator import extract_csharp_changed_symbols_from_diff
from phases.csharp_context_graph import build_csharp_backend_graph, save_backend_graph, load_backend_graph
from phases.backend_risk_propagation import propagate_backend_risk
from phases.backend_context_pack import build_backend_context_pack
from phases.backend_adapter_registry import should_run_backend_review
import phases.backend_review as backend_review
```

New:
```python
from phases.backend_graph_engine import (
    find_symbols_from_diff as _engine_find_symbols,
    build_graph as _engine_build_graph,
    save_graph as _engine_save_graph,
    load_graph as _engine_load_graph,
)
from phases.backend_risk_propagation import propagate_backend_risk
from phases.backend_context_pack import build_backend_context_pack
from phases.backend_adapter_registry import should_run_backend_review, get_adapter
import phases.backend_review as backend_review
```

- [ ] **Step 3: Replace backend analysis block in `cli()`**

Find the block starting with `if phase in (None, "blast") and _should_run_backend_review(diff, cfg):` and replace it entirely with:

```python
    if phase in (None, "blast") and _should_run_backend_review(diff, cfg):
        click.echo("\n[后端] Backend Review Context Engine 分析中...\n")
        from phases.backend_adapter_registry import detect_backend_languages_from_diff as _detect
        detected_langs = _detect(diff)
        enabled = {l.lower() for l in cfg.backend.languages}
        backend_symbols = []
        backend_edges = []

        for lang in detected_langs:
            if lang not in enabled:
                continue
            try:
                adapter = get_adapter(lang)
            except ValueError:
                click.echo(f"  [跳过] {lang}: 适配器尚未实现", err=True)
                continue

            backend_cache = Path(".luna") / "cache" / f"{lang}-graph.json"
            graph = _engine_load_graph(str(backend_cache))
            if graph is None:
                click.echo(f"  构建 {lang} 代码关系图...", err=True)
                graph = _engine_build_graph(adapter, project_root=".")
                _engine_save_graph(graph, str(backend_cache))

            lang_symbols = _engine_find_symbols(diff, adapter, project_root=".")
            backend_symbols.extend(lang_symbols)
            backend_edges.extend(graph.edges)

        if backend_symbols:
            from phases.backend_models import BackendContextGraph as _BCG
            combined_graph = _BCG()
            for e in backend_edges:
                combined_graph.add_edge(e)

            backend_paths = propagate_backend_risk(
                backend_symbols, combined_graph, max_depth=cfg.backend.max_depth
            )
            backend_pack = build_backend_context_pack(backend_symbols, backend_edges, backend_paths)
            backend_items = backend_review.analyze_backend(backend_pack, diff, skill_context, cfg)
            report.backend_review_items = backend_items

            for item in sorted(backend_items, key=lambda x: {"high": 0, "medium": 1, "low": 2}[x.risk]):
                note = " [需人工确认]" if item.needs_human_review else ""
                click.echo(f"[后端·{item.risk}] {item.file}:{item.line} — {item.reason}{note}")
                click.echo(f"  证据: {item.evidence}")
                if item.suggestion and ask("  查看修复建议？"):
                    click.echo(f"  建议: {item.suggestion}")
```

- [ ] **Step 4: Run full test suite**

```bash
pytest -q
```

Expected: all pass

- [ ] **Step 5: Commit**

```bash
git add luna.py
git commit -m "feat: wire engine into luna CLI; support multiple language adapters per diff"
```

---

## Task 7: Migrate C# tests to engine API

The existing `test_csharp_context_graph.py` and `test_csharp_symbol_locator.py` test the old regex modules. Rewrite them to test the engine + adapter API, keeping the same behavioral assertions.

**Files:**
- Replace: `tests/test_csharp_context_graph.py`
- Replace: `tests/test_csharp_symbol_locator.py`

- [ ] **Step 1: Rewrite `tests/test_csharp_context_graph.py`**

```python
# tests/test_csharp_context_graph.py
from pathlib import Path
from phases.adapters.csharp_adapter import CSHARP_ADAPTER
from phases.backend_graph_engine import build_graph, save_graph, load_graph


def test_builds_controller_to_service_edge(tmp_path: Path):
    controller = tmp_path / "Controllers" / "OrderController.cs"
    service = tmp_path / "Services" / "OrderService.cs"
    controller.parent.mkdir()
    service.parent.mkdir()

    controller.write_text(
        "public class OrderController : ControllerBase\n"
        "{\n"
        "    private readonly OrderService _orderService;\n"
        "    [Authorize]\n"
        "    [HttpPost(\"submit\")]\n"
        "    public IActionResult Submit(SubmitOrderRequest request)\n"
        "    {\n"
        "        return Ok(_orderService.Submit(request));\n"
        "    }\n"
        "}\n",
        encoding="utf-8",
    )
    service.write_text(
        "public class OrderService\n"
        "{\n"
        "    public OrderResult Submit(SubmitOrderRequest request)\n"
        "    {\n"
        "        return new OrderResult();\n"
        "    }\n"
        "}\n",
        encoding="utf-8",
    )

    graph = build_graph(CSHARP_ADAPTER, project_root=str(tmp_path))

    assert "Controllers/OrderController.cs:OrderController.Submit" in graph.nodes
    assert "Services/OrderService.cs:OrderService.Submit" in graph.nodes
    assert any(
        e.source == "Controllers/OrderController.cs:OrderController.Submit"
        and e.target == "Services/OrderService.cs:OrderService.Submit"
        and e.edge_type == "calls"
        for e in graph.edges
    )


def test_marks_authorize_attribute_as_auth_edge(tmp_path: Path):
    controller = tmp_path / "Controllers" / "SecureController.cs"
    controller.parent.mkdir()
    controller.write_text(
        "public class SecureController : ControllerBase\n"
        "{\n"
        "    [Authorize]\n"
        "    [HttpGet(\"me\")]\n"
        "    public IActionResult Me() { return Ok(); }\n"
        "}\n",
        encoding="utf-8",
    )

    graph = build_graph(CSHARP_ADAPTER, project_root=str(tmp_path))

    assert any(e.edge_type == "requires_auth" for e in graph.edges)


def test_save_and_load_graph_roundtrip(tmp_path: Path):
    controller = tmp_path / "Controllers" / "OrderController.cs"
    controller.parent.mkdir()
    controller.write_text(
        "public class OrderController : ControllerBase\n"
        "{\n"
        "    [HttpPost]\n"
        "    public IActionResult Submit() { return Ok(); }\n"
        "}\n",
        encoding="utf-8",
    )
    graph = build_graph(CSHARP_ADAPTER, project_root=str(tmp_path))
    cache = tmp_path / "graph.json"
    save_graph(graph, str(cache))

    loaded = load_graph(str(cache))
    assert loaded is not None
    assert set(loaded.nodes.keys()) == set(graph.nodes.keys())
    assert len(loaded.edges) == len(graph.edges)


def test_load_graph_returns_none_for_missing():
    assert load_graph("/nonexistent/graph.json") is None
```

- [ ] **Step 2: Rewrite `tests/test_csharp_symbol_locator.py`**

```python
# tests/test_csharp_symbol_locator.py
from pathlib import Path
from phases.adapters.csharp_adapter import CSHARP_ADAPTER
from phases.backend_graph_engine import find_symbols_from_diff


CSPROJ_DIFF = """\
diff --git a/Controllers/OrderController.cs b/Controllers/OrderController.cs
index aaa..bbb 100644
--- a/Controllers/OrderController.cs
+++ b/Controllers/OrderController.cs
@@ -8,8 +8,9 @@ public class OrderController : ControllerBase
     [Authorize]
     [HttpPost("submit")]
     public IActionResult Submit(SubmitOrderRequest request)
     {
+        if (request.Amount <= 0) return BadRequest();
         var result = _orderService.Submit(request);
         return Ok(result);
     }
"""


def test_extracts_changed_controller_action(tmp_path: Path):
    source = tmp_path / "Controllers" / "OrderController.cs"
    source.parent.mkdir()
    source.write_text(
        "public class OrderController : ControllerBase\n"
        "{\n"
        "    [Authorize]\n"
        "    [HttpPost(\"submit\")]\n"
        "    public IActionResult Submit(SubmitOrderRequest request)\n"
        "    {\n"
        "        if (request.Amount <= 0) return BadRequest();\n"
        "        var result = _orderService.Submit(request);\n"
        "        return Ok(result);\n"
        "    }\n"
        "}\n",
        encoding="utf-8",
    )

    symbols = find_symbols_from_diff(CSPROJ_DIFF, CSHARP_ADAPTER, project_root=str(tmp_path))

    assert len(symbols) == 1
    assert symbols[0].file == "Controllers/OrderController.cs"
    assert symbols[0].class_name == "OrderController"
    assert symbols[0].symbol == "Submit"
    assert symbols[0].symbol_type == "controller_action"
    assert "HttpPost" in symbols[0].attributes
    assert "Authorize" in symbols[0].attributes


def test_extracts_changed_model_property(tmp_path: Path):
    diff = """\
diff --git a/Models/SubmitOrderRequest.cs b/Models/SubmitOrderRequest.cs
index aaa..bbb 100644
--- a/Models/SubmitOrderRequest.cs
+++ b/Models/SubmitOrderRequest.cs
@@ -3,4 +3,4 @@ public class SubmitOrderRequest
-    public decimal? Amount { get; set; }
+    public decimal Amount { get; set; }
"""
    source = tmp_path / "Models" / "SubmitOrderRequest.cs"
    source.parent.mkdir()
    source.write_text(
        "public class SubmitOrderRequest\n"
        "{\n"
        "    public decimal Amount { get; set; }\n"
        "}\n",
        encoding="utf-8",
    )

    symbols = find_symbols_from_diff(diff, CSHARP_ADAPTER, project_root=str(tmp_path))

    assert symbols[0].symbol == "Amount"
    assert symbols[0].symbol_type == "model_property"
    assert symbols[0].class_name == "SubmitOrderRequest"
```

- [ ] **Step 3: Run tests**

```bash
pytest tests/test_csharp_context_graph.py tests/test_csharp_symbol_locator.py -v
```

Expected: all 6 tests pass

- [ ] **Step 4: Commit**

```bash
git add tests/test_csharp_context_graph.py tests/test_csharp_symbol_locator.py
git commit -m "test: migrate C# tests to engine + adapter API"
```

---

## Task 8: Delete deprecated files

**Files:**
- Delete: `phases/csharp_context_graph.py`
- Delete: `phases/csharp_symbol_locator.py`
- Delete: `phases/backend_generic_symbol_locator.py`

- [ ] **Step 1: Delete deprecated modules**

```bash
git rm phases/csharp_context_graph.py phases/csharp_symbol_locator.py phases/backend_generic_symbol_locator.py
```

- [ ] **Step 2: Remove imports from test_backend_pipeline.py**

Read `tests/test_backend_pipeline.py`. Replace:
```python
from phases.csharp_symbol_locator import extract_csharp_changed_symbols_from_diff
from phases.csharp_context_graph import build_csharp_backend_graph
```
with:
```python
from phases.backend_graph_engine import find_symbols_from_diff, build_graph
from phases.adapters.csharp_adapter import CSHARP_ADAPTER
```

Replace usage in the test body:
```python
symbols = extract_csharp_changed_symbols_from_diff(diff, project_root=str(tmp_path))
graph = build_csharp_backend_graph(str(tmp_path))
```
with:
```python
symbols = find_symbols_from_diff(diff, CSHARP_ADAPTER, project_root=str(tmp_path))
graph = build_graph(CSHARP_ADAPTER, project_root=str(tmp_path))
```

- [ ] **Step 3: Check pyproject.toml py-modules**

`pyproject.toml` lists `py-modules` for setuptools. Remove any deleted module names if they appear there. The modules deleted are in `phases/` (a package), not top-level, so no change is needed.

- [ ] **Step 4: Run full suite**

```bash
pytest -v
```

Expected: all pass, no imports from deleted files

- [ ] **Step 5: Commit**

```bash
git add tests/test_backend_pipeline.py
git commit -m "refactor: delete deprecated regex-based C# modules; all tests use engine + adapter"
```

---

## Self-Review

**Spec coverage:**

| Spec requirement | Covered by |
|---|---|
| Engine: file scan, parse, build graph, cache | Task 3 |
| LanguageAdapter Protocol | Task 2 |
| C# adapter with AST-based symbol extraction | Task 4 |
| C# adapter with attribute collection (Authorize, HttpPost) | Task 4 |
| C# adapter with calls edge via field injection | Task 4 |
| C# adapter with writes_db edge (SaveChanges) | Task 4 |
| C# adapter with calls_external_api edge | Task 4 |
| Adapter registry update | Task 5 |
| Luna.py multi-language loop | Task 6 |
| Old C# tests migrated | Task 7 |
| Deprecated files deleted | Task 8 |
| tree-sitter dependency added | Task 1 |
| `phases.adapters` package added | Task 1 + Task 4 |

**No placeholders found.**

**Type consistency:**
- `CSHARP_ADAPTER` exported from `phases/adapters/csharp_adapter.py`, imported in tests and registry — consistent
- Engine functions `find_symbols_from_diff` / `build_graph` / `save_graph` / `load_graph` — names consistent across Tasks 3, 6, 7
- `_engine_find_symbols` / `_engine_build_graph` etc. in luna.py are aliased imports — consistent with engine exports

---

## Note on Plans B and C

Each additional language adapter follows the **exact same pattern** as Task 4:

1. Run the probe script to discover that language's tree-sitter node types
2. Write tests in `tests/adapters/test_xxx_adapter.py` using the same scenario structure
3. Implement `phases/adapters/xxx_adapter.py` with the four Protocol methods
4. Register in `phases/adapters/__init__.py`

Plan B adds: `java_adapter.py` (Spring Boot) + `python_adapter.py` (FastAPI)
Plan C adds: `nodejs_adapter.py` (Express+NestJS) + `go_adapter.py` (Gin) + `php_adapter.py` (Laravel) + `cpp_adapter.py` (gRPC)
