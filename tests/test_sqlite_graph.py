"""Tests for phases/sqlite_graph.py"""
from __future__ import annotations
from pathlib import Path
import sqlite3

import pytest

from phases.sqlite_graph import GraphDB


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _write_ts(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")


# ---------------------------------------------------------------------------
# Task 1: schema + build
# ---------------------------------------------------------------------------

def test_build_creates_nodes_for_ts_files(tmp_path):
    _write_ts(tmp_path / "app.ts", "export function hello() {}\n")
    db = GraphDB(str(tmp_path / "graph.db"))
    db.build(str(tmp_path))
    rows = db._conn.execute("SELECT id FROM nodes WHERE file='app.ts'").fetchall()
    assert len(rows) >= 1


def test_build_skips_node_modules(tmp_path):
    nm = tmp_path / "node_modules" / "lib"
    nm.mkdir(parents=True)
    _write_ts(nm / "index.ts", "export function skip() {}\n")
    _write_ts(tmp_path / "src.ts", "export function keep() {}\n")
    db = GraphDB(str(tmp_path / "graph.db"))
    db.build(str(tmp_path))
    files = {r[0] for r in db._conn.execute("SELECT file FROM nodes").fetchall()}
    assert not any("node_modules" in f for f in files)
    assert any("src.ts" in f for f in files)


def test_node_has_file_hash(tmp_path):
    _write_ts(tmp_path / "app.ts", "export function hello() {}\n")
    db = GraphDB(str(tmp_path / "graph.db"))
    db.build(str(tmp_path))
    rows = db._conn.execute(
        "SELECT file_hash FROM nodes WHERE node_type='file'"
    ).fetchall()
    assert rows
    assert all(len(r[0]) == 64 for r in rows)  # SHA-256 hex = 64 chars


# ---------------------------------------------------------------------------
# Task 2: incremental update
# ---------------------------------------------------------------------------

def test_update_only_reparses_changed_file(tmp_path):
    f_a = tmp_path / "a.ts"
    f_b = tmp_path / "b.ts"
    _write_ts(f_a, "export function alpha() {}\n")
    _write_ts(f_b, "export function beta() {}\n")

    db = GraphDB(str(tmp_path / "graph.db"))
    db.build(str(tmp_path))

    hash_b_before = db._conn.execute(
        "SELECT file_hash FROM nodes WHERE file='b.ts' AND node_type='file'"
    ).fetchone()[0]

    # Modify a.ts, leave b.ts untouched
    _write_ts(f_a, "export function alpha_v2() {}\n")
    db.update(str(tmp_path))

    hash_b_after = db._conn.execute(
        "SELECT file_hash FROM nodes WHERE file='b.ts' AND node_type='file'"
    ).fetchone()[0]
    assert hash_b_before == hash_b_after  # b.ts 未重解析，hash 不变

    # a.ts should now have the new export symbol
    names = {r[0] for r in db._conn.execute("SELECT name FROM nodes WHERE file='a.ts'").fetchall()}
    assert "alpha_v2" in names


def test_update_removes_deleted_file_nodes(tmp_path):
    f_a = tmp_path / "a.ts"
    f_b = tmp_path / "b.ts"
    _write_ts(f_a, "export function alpha() {}\n")
    _write_ts(f_b, "export function beta() {}\n")

    db = GraphDB(str(tmp_path / "graph.db"))
    db.build(str(tmp_path))

    f_b.unlink()
    db.update(str(tmp_path))

    files = {r[0] for r in db._conn.execute("SELECT file FROM nodes").fetchall()}
    assert not any("b.ts" in f for f in files)


# ---------------------------------------------------------------------------
# Task 3: BFS via RECURSIVE CTE
# ---------------------------------------------------------------------------

def _seed_db(db: GraphDB, edges: list[tuple[str, str]]) -> None:
    """Directly insert file nodes + import edges for BFS tests."""
    files = {f for pair in edges for f in pair}
    with db._conn:
        for f in files:
            db._conn.execute(
                "INSERT OR IGNORE INTO nodes(id, node_type, file, name, file_hash) VALUES (?,?,?,?,?)",
                (f, "file", f, f, ""),
            )
        for src, tgt in edges:
            db._conn.execute(
                "INSERT INTO edges(source, target, edge_type, file) VALUES (?,?,?,?)",
                (src, tgt, "imports", src),
            )


def test_bfs_impact_finds_two_hop_importers(tmp_path):
    # A imports B, B imports C → from seed=A, should find B and C
    db = GraphDB(str(tmp_path / "graph.db"))
    _seed_db(db, [("a.ts", "b.ts"), ("b.ts", "c.ts")])
    result = {r["node"] for r in db.bfs_impact(["a.ts"], max_depth=3)}
    assert "b.ts" in result
    assert "c.ts" in result


def test_bfs_impact_respects_max_depth(tmp_path):
    db = GraphDB(str(tmp_path / "graph.db"))
    _seed_db(db, [("a.ts", "b.ts"), ("b.ts", "c.ts")])
    result = {r["node"] for r in db.bfs_impact(["a.ts"], max_depth=1)}
    assert "b.ts" in result
    assert "c.ts" not in result  # depth=1 only reaches b.ts


def test_bfs_impact_deduplicates(tmp_path):
    # Circular: a → b → a (should not loop)
    db = GraphDB(str(tmp_path / "graph.db"))
    _seed_db(db, [("a.ts", "b.ts"), ("b.ts", "a.ts")])
    result = db.bfs_impact(["a.ts"], max_depth=5)
    nodes = [r["node"] for r in result]
    assert nodes.count("b.ts") == 1  # no duplicates


def test_get_importers_returns_direct_importers(tmp_path):
    db = GraphDB(str(tmp_path / "graph.db"))
    _seed_db(db, [("a.ts", "shared.ts"), ("b.ts", "shared.ts")])
    importers = set(db.get_importers("shared.ts"))
    assert importers == {"a.ts", "b.ts"}


# ---------------------------------------------------------------------------
# Task 4: compatibility layer
# ---------------------------------------------------------------------------

def test_full_pipeline_uses_sqlite_graph(tmp_path):
    """build_graph() → propagate_risk() should work end-to-end via SQLite."""
    from phases.context_graph import build_graph
    from phases.risk_propagation import propagate_risk
    from phases.symbol_locator import ChangedSymbol

    # a.ts exports doAuth, b.ts imports a.ts
    _write_ts(tmp_path / "a.ts", "export function doAuth() {}\n")
    _write_ts(tmp_path / "b.ts", "import { doAuth } from './a';\n")

    graph = build_graph(str(tmp_path))
    sym = ChangedSymbol(file="a.ts", symbol="doAuth",
                        symbol_type="function", start_line=1, change_type="modified")
    paths = propagate_risk([sym], graph)
    affected = {f for p in paths for f in p.path}
    assert "b.ts" in affected


def test_build_graph_creates_db_file(tmp_path):
    from phases.context_graph import build_graph
    _write_ts(tmp_path / "app.ts", "export function main() {}\n")
    build_graph(str(tmp_path))
    db_path = tmp_path / ".luna" / "cache" / "context-graph.db"
    assert db_path.exists()


# ---------------------------------------------------------------------------
# Task 5: schema migration
# ---------------------------------------------------------------------------

def test_fts5_index_populated_after_build(tmp_path):
    """build() 之后 nodes_fts 里应能搜到导出的符号名。"""
    _write_ts(tmp_path / "auth.ts", "export function handleLogin() {}\n")
    db = GraphDB(str(tmp_path / "graph.db"))
    db.build(str(tmp_path))
    results = db.fts_search("handleLogin")
    assert any("auth.ts" in r["file"] for r in results)


def test_schema_version_mismatch_triggers_rebuild(tmp_path):
    db_path = str(tmp_path / "graph.db")
    # First build — version 1
    db = GraphDB(db_path)
    _write_ts(tmp_path / "app.ts", "export function hello() {}\n")
    db.build(str(tmp_path))
    db.close()

    # Tamper: write wrong version into the DB
    conn = sqlite3.connect(db_path)
    conn.execute("UPDATE schema_version SET version = 999")
    conn.commit()
    conn.close()

    # Reopening should detect mismatch and rebuild (no crash, fresh schema)
    db2 = GraphDB(db_path)
    from phases.sqlite_graph import _CURRENT_VERSION
    version = db2._conn.execute("SELECT version FROM schema_version").fetchone()[0]
    assert version == _CURRENT_VERSION  # 重建后版本号恢复为当前版本
    db2.close()
