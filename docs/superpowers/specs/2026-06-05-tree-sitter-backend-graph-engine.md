# Tree-sitter Backend Graph Engine Design

**Date:** 2026-06-05
**Status:** Approved
**Goal:** Replace regex-based backend analysis with a unified tree-sitter engine + per-language adapters. Precision is first priority.

---

## Problem

Current state:
- C# uses regex (csharp_symbol_locator.py + csharp_context_graph.py): misses multi-line signatures, generics, partial classes
- Python/Java/Go/PHP/Node.js/C++ use a generic regex locator: no call graph, no framework-specific edges
- 7 separate implementations would mean ~2100 lines of duplicated boilerplate

Goal: one engine, 7 thin adapters, tree-sitter AST precision for all languages.

---

## Architecture

Three layers:

```
Engine (phases/backend_graph_engine.py)       — written once
    ↑ uses
Adapter Protocol (phases/backend_language_adapter.py)  — contract
    ↑ implemented by
Language Adapters (phases/adapters/xxx_adapter.py × 7) — ~60-80 lines each
```

### Engine responsibilities
- Scan project files by extension
- Call tree-sitter to parse each file
- Call adapter methods to extract nodes + edges
- Build BackendContextGraph
- Save/load cache at `.luna/cache/<language>-graph.json`
- Find enclosing symbol for a given diff line number

### Adapter responsibilities
- Provide the tree-sitter Language object
- Provide tree-sitter queries that match framework-specific patterns
- Classify extracted nodes (controller_action / service_method / repository_method / model_property / …)
- Know what counts as a high-risk edge (auth, DB write, external HTTP call)

---

## Adapter Protocol

```python
class LanguageAdapter(Protocol):
    name: str                        # "python", "java", …
    extensions: tuple[str, ...]      # (".py",), (".java",), …

    def get_language(self) -> Any:
        # Returns tree-sitter Language object (lazy-loaded)

    def find_enclosing_symbol(
        self,
        root_node,
        source: bytes,
        line: int,           # 1-based line number
        rel_path: str,
        is_new_file: bool,
    ) -> BackendChangedSymbol | None:
        # Diff analysis: given a changed line, return the enclosing function/method.
        # Uses tree-sitter to walk the AST upward from the line position.

    def extract_file_nodes(
        self,
        root_node,
        source: bytes,
        rel_path: str,
    ) -> list[BackendGraphNode]:
        # Graph building: extract all symbols from a file.
        # Uses tree-sitter queries to find functions, classes, methods, decorated handlers.

    def extract_file_edges(
        self,
        root_node,
        source: bytes,
        rel_path: str,
        method_index: dict[str, str],  # "ClassName.method" → node_id
    ) -> list[BackendGraphEdge]:
        # Graph building: extract call edges, auth edges, DB edges, import edges.
        # Uses tree-sitter queries to find call expressions and match against method_index.
```

---

## Language Coverage

| Language | Framework | Tree-sitter package |
|----------|-----------|---------------------|
| C# | ASP.NET Core Controller | `tree-sitter-c-sharp` |
| Java | Spring Boot | `tree-sitter-java` |
| Python | FastAPI | `tree-sitter-python` |
| Node.js | Express + NestJS | `tree-sitter-javascript` + `tree-sitter-typescript` |
| Go | Gin | `tree-sitter-go` |
| PHP | Laravel | `tree-sitter-php` |
| C++ | gRPC | `tree-sitter-cpp` |

### Per-language precision targets

**C# (ASP.NET Core)**
- Symbols: controller actions with `[HttpGet/Post/Put/Delete]`, service methods, repository methods, DTO/model properties
- Edges: `requires_auth` from `[Authorize]`, `writes_db` from `SaveChanges/SaveChangesAsync`, `calls_external_api` from `HttpClient`, `calls` via `_field.Method()` injection pattern
- High-risk patterns: nullable→non-nullable property change, `[Required]` added/removed

**Java (Spring Boot)**
- Symbols: `@RestController`/`@Controller` methods with `@GetMapping`/`@PostMapping`/etc, `@Service` methods, `@Repository` methods, `@Entity` fields
- Edges: `requires_auth` from `@PreAuthorize`/`@Secured`, `writes_db` from `save()`/`delete()` on JpaRepository, `calls` via `@Autowired` field injection, `transaction_boundary` from `@Transactional`
- High-risk patterns: `@Transactional` added/removed, `@Valid` removed from parameter

**Python (FastAPI)**
- Symbols: functions with `@router.get/post/put/delete/patch`, `@app.get/post/…`, Pydantic model fields, service functions, ORM model fields
- Edges: `requires_auth` from `Depends(get_current_user)` or `Security()`, `writes_db` from `.save()`/`.commit()`/`session.add()`, `calls` via function call resolution
- High-risk patterns: removing auth dependency, changing Pydantic field from required to optional

**Node.js (Express + NestJS)**
- Symbols: `router.get/post/put/delete` handlers, `@Controller`/`@Get`/`@Post` decorated methods (NestJS), middleware functions
- Edges: `requires_auth` from `AuthGuard`/`passport`/`jwt` middleware, `writes_db` from `.save()`/`prisma.xxx.create()`/`repository.save()`, `calls` via function reference
- High-risk patterns: auth guard removed, validation pipe removed

**Go (Gin)**
- Symbols: functions with `gin.Context` parameter, service functions, repository functions, struct fields with `json` tags
- Edges: `requires_auth` from middleware chaining pattern, `writes_db` from `db.Save()`/`db.Create()`/`tx.Commit()`, `calls` via direct function call
- High-risk patterns: `json` struct tag changed, context/auth middleware removed from route group

**PHP (Laravel)**
- Symbols: controller methods (class extending `Controller`), `FormRequest` subclass methods, `Model` subclass methods, middleware `handle` methods
- Edges: `requires_auth` from `$this->middleware('auth')` or `auth()` facade, `writes_db` from `->save()`/`->delete()`/`DB::transaction()`, `calls` via method call on injected service
- High-risk patterns: `authorize()` removed from FormRequest, `->validated()` bypassed

**C++ (gRPC)**
- Symbols: method overrides of generated service base classes, handler functions with `grpc::ServerContext*` parameter, struct fields
- Edges: `calls` via direct method call, `writes_storage` from storage API calls, `starts_thread` from `std::thread`/`std::async`, `locks_mutex` from `std::mutex::lock()`
- High-risk patterns: pointer/reference semantics changed, mutex removed around shared state

---

## Data Flow

### Diff analysis (replaces symbol locators)

```
git diff
  → parse_diff() → DiffFile[] (unchanged, from symbol_locator.py)
  → For each changed .cs/.py/.java/… file:
      tree-sitter parse file
      adapter.find_enclosing_symbol(root, source, changed_line)
      → BackendChangedSymbol
  → list[BackendChangedSymbol]
```

### Graph building (replaces csharp_context_graph + generic locator)

```
project_root
  → engine scans files by adapter.extensions
  → For each file:
      tree-sitter parse
      adapter.extract_file_nodes() → BackendGraphNode[]
  → Build method_index: "Class.method" → node_id
  → For each file:
      adapter.extract_file_edges(method_index) → BackendGraphEdge[]
  → BackendContextGraph
  → save to .luna/cache/<language>-graph.json
```

### Luna CLI routing (replaces current if/else in luna.py)

```python
detected_langs = detect_backend_languages_from_diff(diff)  # unchanged
for lang in detected_langs:
    adapter = get_adapter(lang)           # new: registry returns adapter
    symbols = engine.find_symbols(diff, adapter)
    graph = engine.load_or_build_graph(adapter, project_root)
    paths = propagate_backend_risk(symbols, graph)
    pack = build_backend_context_pack(symbols, graph.edges, paths)
    items = backend_review.analyze_backend(pack, diff, skill_context, cfg)
```

---

## File Map

### New files

| Path | Responsibility |
|------|----------------|
| `phases/backend_graph_engine.py` | Unified engine: file scan, parse, graph build, cache, symbol find |
| `phases/backend_language_adapter.py` | `LanguageAdapter` Protocol + `SymbolInfo` dataclass |
| `phases/adapters/__init__.py` | Adapter registry: `get_adapter(name) → LanguageAdapter` |
| `phases/adapters/csharp_adapter.py` | C# tree-sitter queries + classification |
| `phases/adapters/java_adapter.py` | Java/Spring Boot queries + classification |
| `phases/adapters/python_adapter.py` | Python/FastAPI queries + classification |
| `phases/adapters/nodejs_adapter.py` | JS/TS Express+NestJS queries + classification |
| `phases/adapters/go_adapter.py` | Go/Gin queries + classification |
| `phases/adapters/php_adapter.py` | PHP/Laravel queries + classification |
| `phases/adapters/cpp_adapter.py` | C++/gRPC queries + classification |

### Deleted files (logic migrated)

| Path | Replaced by |
|------|-------------|
| `phases/csharp_context_graph.py` | `csharp_adapter.py` + `backend_graph_engine.py` |
| `phases/csharp_symbol_locator.py` | `csharp_adapter.py` + `backend_graph_engine.py` |
| `phases/backend_generic_symbol_locator.py` | all adapters + `backend_graph_engine.py` |

### Modified files

| Path | Change |
|------|--------|
| `pyproject.toml` | Add tree-sitter + 7 grammar packages |
| `phases/backend_adapter_registry.py` | `get_adapter(lang) → LanguageAdapter` |
| `luna.py` | Use engine API; loop over detected languages |
| `tests/test_csharp_*.py` | Update imports; behaviour unchanged |

---

## Dependencies

Add to `pyproject.toml`:

```toml
[project.dependencies]
tree-sitter>=0.23
tree-sitter-c-sharp>=0.23
tree-sitter-java>=0.23
tree-sitter-python>=0.23
tree-sitter-javascript>=0.23
tree-sitter-typescript>=0.23
tree-sitter-go>=0.23
tree-sitter-php>=0.23
tree-sitter-cpp>=0.23
```

---

## Testing Strategy

- Each adapter has its own test file (`tests/adapters/test_xxx_adapter.py`)
- Tests use `tmp_path` fixture with realistic framework-specific source files
- Each adapter test covers: symbol extraction, import edges, auth edge, DB edge, enclosing symbol lookup
- Engine tests cover: file scan, cache roundtrip, multi-language project
- Existing `test_csharp_*.py` tests are updated (imports change, assertions unchanged)
- End-to-end pipeline test updated in `test_backend_pipeline.py`

---

## Out of Scope

- Visual graph output (Mermaid/DOT)
- Multiple frameworks per language in first pass (one primary framework per language)
- Cross-language call edges (e.g., Python calling Java via REST)
- Automatic graph cache invalidation on file change (manual delete or `--rebuild-graph` flag, future)
