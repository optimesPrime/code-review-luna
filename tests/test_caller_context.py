"""Tests for phases/caller_context.py — Task 1 & 2"""
from __future__ import annotations
from pathlib import Path

from phases.caller_context import (
    grep_call_sites,
    extract_call_snippet,
    build_caller_contexts,
)
from phases.symbol_locator import ChangedSymbol


def _sym(symbol: str, file: str) -> ChangedSymbol:
    return ChangedSymbol(file=file, symbol=symbol, symbol_type="function", start_line=1, change_type="modified")


# ---------------------------------------------------------------------------
# grep_call_sites
# ---------------------------------------------------------------------------

def test_grep_finds_caller_in_python_file(tmp_path):
    caller = tmp_path / "app.py"
    caller.write_text("x = 1\nresult = build_graph('.')\nprint(result)\n")
    hits = grep_call_sites("build_graph", str(tmp_path), ignore_dirs=[], self_file=None)
    assert any(h[0].endswith("app.py") and h[1] == 2 for h in hits)


def test_grep_finds_caller_in_typescript_file(tmp_path):
    caller = tmp_path / "main.ts"
    caller.write_text("import {build_graph} from './graph';\nconst g = build_graph();\n")
    hits = grep_call_sites("build_graph", str(tmp_path), ignore_dirs=[], self_file=None)
    assert any(h[0].endswith("main.ts") and h[1] == 2 for h in hits)


def test_grep_excludes_self_file(tmp_path):
    self_file = tmp_path / "graph.py"
    self_file.write_text("def build_graph():\n    pass\nbuild_graph()\n")
    other = tmp_path / "caller.py"
    other.write_text("from graph import build_graph\nbuild_graph()\n")

    hits = grep_call_sites(
        "build_graph", str(tmp_path), ignore_dirs=[],
        self_file=str(self_file),
    )
    filenames = [h[0] for h in hits]
    assert not any("graph.py" in f for f in filenames)
    assert any("caller.py" in f for f in filenames)


def test_grep_excludes_comment_lines(tmp_path):
    caller = tmp_path / "notes.py"
    caller.write_text(
        "# build_graph() — do not call directly\n"
        "// build_graph() in JS comment\n"
        "result = build_graph('.')\n"
    )
    hits = grep_call_sites("build_graph", str(tmp_path), ignore_dirs=[], self_file=None)
    # Only the real call on line 3 should appear, not the comment lines
    lines = [h[1] for h in hits if h[0].endswith("notes.py")]
    assert lines == [3]


def test_grep_returns_empty_for_unused_symbol(tmp_path):
    other = tmp_path / "app.py"
    other.write_text("x = 1\nprint(x)\n")
    hits = grep_call_sites("totally_unused_symbol_xyz", str(tmp_path), ignore_dirs=[], self_file=None)
    assert hits == []


def test_grep_respects_ignore_dirs(tmp_path):
    ignored = tmp_path / "node_modules" / "lib"
    ignored.mkdir(parents=True)
    (ignored / "index.js").write_text("build_graph();\n")
    normal = tmp_path / "src"
    normal.mkdir()
    (normal / "app.py").write_text("build_graph()\n")

    hits = grep_call_sites(
        "build_graph", str(tmp_path),
        ignore_dirs=["node_modules"],
        self_file=None,
    )
    filenames = [h[0] for h in hits]
    assert not any("node_modules" in f for f in filenames)
    assert any("app.py" in f for f in filenames)


def test_grep_excludes_python_import_line(tmp_path):
    f = tmp_path / "app.py"
    f.write_text("from graph import build_graph\nresult = build_graph('.')\n")
    hits = grep_call_sites("build_graph", str(tmp_path), ignore_dirs=[], self_file=None)
    lines = [h[1] for h in hits if h[0].endswith("app.py")]
    assert lines == [2]  # import 行（第1行）不应出现


def test_grep_excludes_ts_import_line(tmp_path):
    f = tmp_path / "main.ts"
    f.write_text(
        "import { buildGraph } from './graph';\n"
        "import type { buildGraph } from './types';\n"
        "const g = buildGraph();\n"
    )
    hits = grep_call_sites("buildGraph", str(tmp_path), ignore_dirs=[], self_file=None)
    lines = [h[1] for h in hits if h[0].endswith("main.ts")]
    assert lines == [3]  # 只保留第3行真实调用


def test_grep_excludes_type_annotation_parameter(tmp_path):
    f = tmp_path / "app.py"
    f.write_text(
        "def process(g: build_graph) -> None:\n"
        "    pass\n"
        "result = build_graph('.')\n"
    )
    hits = grep_call_sites("build_graph", str(tmp_path), ignore_dirs=[], self_file=None)
    lines = [h[1] for h in hits if h[0].endswith("app.py")]
    assert lines == [3]  # 第1行是纯类型注解，不应出现


def test_grep_excludes_return_type_annotation(tmp_path):
    f = tmp_path / "app.py"
    f.write_text(
        "def factory() -> build_graph:\n"
        "    return build_graph('.')\n"
    )
    hits = grep_call_sites("build_graph", str(tmp_path), ignore_dirs=[], self_file=None)
    lines = [h[1] for h in hits if h[0].endswith("app.py")]
    assert lines == [2]  # 第1行 -> 类型注解不应出现，第2行实例化应保留


def test_grep_keeps_instantiation_line(tmp_path):
    f = tmp_path / "app.py"
    f.write_text("graph = build_graph('.')\n")
    hits = grep_call_sites("build_graph", str(tmp_path), ignore_dirs=[], self_file=None)
    assert any(h[0].endswith("app.py") and h[1] == 1 for h in hits)


def test_grep_keeps_attribute_access_line(tmp_path):
    f = tmp_path / "app.py"
    f.write_text("nodes = build_graph.nodes\n")
    hits = grep_call_sites("build_graph", str(tmp_path), ignore_dirs=[], self_file=None)
    assert any(h[0].endswith("app.py") and h[1] == 1 for h in hits)


def test_grep_keeps_isinstance_check(tmp_path):
    # isinstance 没有 symbol( 或 symbol.，但也没有类型上下文标记 → 保留
    f = tmp_path / "app.py"
    f.write_text("assert isinstance(g, build_graph)\n")
    hits = grep_call_sites("build_graph", str(tmp_path), ignore_dirs=[], self_file=None)
    assert any(h[0].endswith("app.py") and h[1] == 1 for h in hits)


# ---------------------------------------------------------------------------
# extract_call_snippet
# ---------------------------------------------------------------------------

def test_extract_call_snippet_returns_surrounding_lines(tmp_path):
    f = tmp_path / "app.py"
    lines = [f"line{i}\n" for i in range(1, 21)]  # 20 lines
    f.write_text("".join(lines))
    snippet = extract_call_snippet(str(f), line=10, context_lines=5)
    assert "line10" in snippet
    assert "line5" in snippet
    assert "line15" in snippet


def test_extract_call_snippet_truncates_at_max_lines(tmp_path):
    f = tmp_path / "app.py"
    lines = [f"line{i}\n" for i in range(1, 101)]
    f.write_text("".join(lines))
    snippet = extract_call_snippet(str(f), line=50, context_lines=5)
    assert snippet.count("\n") <= 13  # 12 lines + possible truncation marker


def test_extract_call_snippet_returns_empty_for_missing_file(tmp_path):
    snippet = extract_call_snippet(str(tmp_path / "nonexistent.py"), line=1)
    assert snippet == ""


# ---------------------------------------------------------------------------
# build_caller_contexts
# ---------------------------------------------------------------------------

def test_build_caller_contexts_per_symbol(tmp_path):
    (tmp_path / "caller.py").write_text("from g import build_graph\nbuild_graph()\n")
    sym_file = str(tmp_path / "g.py")
    Path(sym_file).write_text("def build_graph(): pass\n")
    symbols = [_sym("build_graph", sym_file)]
    results = build_caller_contexts(symbols, str(tmp_path), ignore_dirs=[])
    assert len(results) == 1
    assert results[0].symbol == "build_graph"
    assert len(results[0].callers) >= 1


def test_caller_contexts_caps_per_symbol(tmp_path):
    # 创建 10 个调用者文件，但 max_callers_per_symbol=3
    for i in range(10):
        (tmp_path / f"caller{i}.py").write_text(f"import g\ng.my_func()\n")
    sym_file = str(tmp_path / "g.py")
    Path(sym_file).write_text("def my_func(): pass\n")
    symbols = [_sym("my_func", sym_file)]
    results = build_caller_contexts(
        symbols, str(tmp_path), ignore_dirs=[], max_callers_per_symbol=3
    )
    assert len(results[0].callers) <= 3


def test_caller_contexts_records_total_count(tmp_path):
    for i in range(6):
        (tmp_path / f"caller{i}.py").write_text("import g\ng.my_func()\n")
    sym_file = str(tmp_path / "g.py")
    Path(sym_file).write_text("def my_func(): pass\n")
    symbols = [_sym("my_func", sym_file)]
    results = build_caller_contexts(
        symbols, str(tmp_path), ignore_dirs=[], max_callers_per_symbol=3
    )
    assert results[0].total_count >= 6
    assert len(results[0].callers) == 3


def test_caller_contexts_total_cap_enforced(tmp_path):
    # 2 个 symbol，每个 5 个调用者，total_callers_cap=6
    for i in range(5):
        (tmp_path / f"a_caller{i}.py").write_text("import m\nm.func_a()\n")
        (tmp_path / f"b_caller{i}.py").write_text("import m\nm.func_b()\n")
    sym_file = str(tmp_path / "m.py")
    Path(sym_file).write_text("def func_a(): pass\ndef func_b(): pass\n")
    symbols = [_sym("func_a", sym_file), _sym("func_b", sym_file)]
    results = build_caller_contexts(
        symbols, str(tmp_path), ignore_dirs=[],
        max_callers_per_symbol=5, total_callers_cap=6,
    )
    total_shown = sum(len(r.callers) for r in results)
    assert total_shown <= 6


def test_caller_contexts_skips_self_file(tmp_path):
    sym_file = tmp_path / "g.py"
    sym_file.write_text("def build_graph(): pass\nbuild_graph()\n")
    (tmp_path / "caller.py").write_text("from g import build_graph\nbuild_graph()\n")
    symbols = [_sym("build_graph", str(sym_file))]
    results = build_caller_contexts(symbols, str(tmp_path), ignore_dirs=[])
    for sc in results:
        for caller in sc.callers:
            assert "g.py" not in caller.file
