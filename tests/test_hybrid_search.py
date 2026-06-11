"""Tests for phases/hybrid_search.py"""
from __future__ import annotations
from pathlib import Path

from phases.sqlite_graph import GraphDB
from phases.hybrid_search import (
    fts_search,
    rrf_merge,
    detect_query_kind_boost,
    hybrid_search,
    HybridResult,
)


def _write(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")


def _db(tmp_path: Path) -> GraphDB:
    db = GraphDB(str(tmp_path / "graph.db"))
    return db


# ---------------------------------------------------------------------------
# Task 1: fts_search
# ---------------------------------------------------------------------------

def test_fts_search_finds_symbol_by_name(tmp_path):
    _write(tmp_path / "auth.ts", "export function handleSubmit() {}\n")
    db = _db(tmp_path)
    db.build(str(tmp_path))
    results = fts_search(db, "handleSubmit")
    assert any("auth.ts" in r for r in results)


def test_fts_search_case_insensitive(tmp_path):
    _write(tmp_path / "form.ts", "export function HandleSubmit() {}\n")
    db = _db(tmp_path)
    db.build(str(tmp_path))
    results = fts_search(db, "handlesubmit")
    assert any("form.ts" in r for r in results)


def test_fts_search_returns_empty_for_unknown_symbol(tmp_path):
    _write(tmp_path / "app.ts", "export function knownFunc() {}\n")
    db = _db(tmp_path)
    db.build(str(tmp_path))
    assert fts_search(db, "totallyUnknownXyz999") == []


# ---------------------------------------------------------------------------
# Task 2: rrf_merge + detect_query_kind_boost
# ---------------------------------------------------------------------------

def test_rrf_merge_combines_two_lists():
    a = ["file_a.ts", "file_b.ts", "file_c.ts"]
    b = ["file_b.ts", "file_d.ts"]
    results = rrf_merge([a, b])
    files = [r for r, _ in results]
    # file_b.ts 在两路都出现，应排在前面
    assert files.index("file_b.ts") < files.index("file_a.ts")
    assert files.index("file_b.ts") < files.index("file_d.ts")


def test_rrf_merge_deduplicates():
    a = ["x.ts", "y.ts"]
    b = ["x.ts", "z.ts"]
    results = rrf_merge([a, b])
    files = [r for r, _ in results]
    assert files.count("x.ts") == 1


def test_rrf_merge_empty_lists():
    assert rrf_merge([]) == []
    assert rrf_merge([[], []]) == []


def test_detect_query_kind_boost_pascal_case():
    boost = detect_query_kind_boost("UserStore")
    assert boost.get("class", 0) > 1.0


def test_detect_query_kind_boost_snake_case():
    boost = detect_query_kind_boost("get_user_by_id")
    assert boost.get("function", 0) > 1.0


def test_detect_query_kind_boost_no_pattern():
    boost = detect_query_kind_boost("foo")
    assert boost == {}


# ---------------------------------------------------------------------------
# Task 2: hybrid_search 主函数
# ---------------------------------------------------------------------------

def test_hybrid_search_returns_hybrid_results(tmp_path):
    _write(tmp_path / "auth.ts", "export function handleLogin() {}\n")
    _write(tmp_path / "caller.ts", "import { handleLogin } from './auth';\n")
    db = _db(tmp_path)
    db.build(str(tmp_path))
    from phases.symbol_locator import ChangedSymbol
    sym = ChangedSymbol(file="auth.ts", symbol="handleLogin",
                        symbol_type="function", start_line=1, change_type="modified")
    results = hybrid_search(db, [sym], project_root=str(tmp_path))
    assert isinstance(results, list)
    assert all(isinstance(r, HybridResult) for r in results)


def test_hybrid_search_augments_impact_paths(tmp_path):
    """hybrid_search 的结果应被注入到 context_pack.impact_paths 中。"""
    from phases.hybrid_search import augment_impact_paths
    from phases.risk_propagation import ImpactPath
    from phases.symbol_locator import ChangedSymbol

    _write(tmp_path / "utils.ts", "export function doAuth() {}\n")
    _write(tmp_path / "spec.ts", "doAuth();\n")  # 引用但不 import
    db = _db(tmp_path)
    db.build(str(tmp_path))

    sym = ChangedSymbol(file="utils.ts", symbol="doAuth",
                        symbol_type="function", start_line=1, change_type="modified")
    existing_paths: list[ImpactPath] = []
    augmented = augment_impact_paths(
        existing_paths, db, [sym], project_root=str(tmp_path)
    )
    files_in_paths = {p.path[-1] for p in augmented}
    assert any("spec" in f for f in files_in_paths)


def test_hybrid_search_fts_finds_non_import_reference(tmp_path):
    """grep route 应能找到「引用但没有 import」的文件（如测试文件）。"""
    _write(tmp_path / "utils.ts", "export function doAuth() {}\n")
    # spec 文件不 import utils，但内容里有 doAuth
    _write(tmp_path / "utils.spec.ts",
           "describe('doAuth', () => { doAuth(); });\n")
    db = _db(tmp_path)
    db.build(str(tmp_path))
    from phases.symbol_locator import ChangedSymbol
    sym = ChangedSymbol(file="utils.ts", symbol="doAuth",
                        symbol_type="function", start_line=1, change_type="modified")
    results = hybrid_search(db, [sym], project_root=str(tmp_path))
    files = [r.file for r in results]
    assert any("spec" in f for f in files)
