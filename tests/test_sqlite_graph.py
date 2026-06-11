from __future__ import annotations

import hashlib
from pathlib import Path

import pytest


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _ts(tmp_path: Path, rel: str, content: str) -> Path:
    p = tmp_path / rel
    _write(p, content)
    return p


class TestGraphDBBuild:
    def test_build_creates_db_file(self, tmp_path):
        from phases.sqlite_graph import GraphDB
        db = GraphDB(str(tmp_path / "graph.db"))
        _ts(tmp_path, "src/a.ts", "export function foo() {}")
        db.build(str(tmp_path))
        assert (tmp_path / "graph.db").exists()

    def test_build_creates_nodes_for_ts_files(self, tmp_path):
        from phases.sqlite_graph import GraphDB
        _ts(tmp_path, "src/auth.ts", "export function login() {}")
        _ts(tmp_path, "src/utils.ts", "export function helper() {}")
        db = GraphDB(str(tmp_path / "g.db"))
        db.build(str(tmp_path))
        nodes = db.get_all_files()
        rel_nodes = [n.replace(str(tmp_path.resolve()) + "/", "") for n in nodes]
        assert "src/auth.ts" in rel_nodes
        assert "src/utils.ts" in rel_nodes

    def test_build_stores_file_hash(self, tmp_path):
        from phases.sqlite_graph import GraphDB
        content = "export function foo() {}"
        _ts(tmp_path, "src/a.ts", content)
        db = GraphDB(str(tmp_path / "g.db"))
        db.build(str(tmp_path))
        expected = hashlib.sha256(content.encode()).hexdigest()
        stored = db.get_file_hash(str((tmp_path / "src/a.ts").resolve()))
        assert stored == expected

    def test_build_skips_node_modules(self, tmp_path):
        from phases.sqlite_graph import GraphDB
        _ts(tmp_path, "node_modules/lib/index.ts", "export function x() {}")
        _ts(tmp_path, "src/a.ts", "export function foo() {}")
        db = GraphDB(str(tmp_path / "g.db"))
        db.build(str(tmp_path))
        files = db.get_all_files()
        assert not any("/node_modules/" in f or f.endswith("/node_modules") for f in files)

    def test_build_creates_import_edges(self, tmp_path):
        from phases.sqlite_graph import GraphDB
        _ts(tmp_path, "src/a.ts", "export function foo() {}")
        _ts(tmp_path, "src/b.ts", "import { foo } from './a'")
        db = GraphDB(str(tmp_path / "g.db"))
        db.build(str(tmp_path))
        importers = db.get_importers(str((tmp_path / "src/a.ts").resolve()))
        rel = [i.replace(str(tmp_path.resolve()) + "/", "") for i in importers]
        assert "src/b.ts" in rel

    def test_get_importers_returns_empty_for_unknown_file(self, tmp_path):
        from phases.sqlite_graph import GraphDB
        db = GraphDB(str(tmp_path / "g.db"))
        db.build(str(tmp_path))
        assert db.get_importers("/nonexistent/file.ts") == []

    def test_build_handles_empty_project(self, tmp_path):
        from phases.sqlite_graph import GraphDB
        db = GraphDB(str(tmp_path / "g.db"))
        db.build(str(tmp_path))
        assert db.get_all_files() == []


class TestGraphDBUpdate:
    def test_update_only_reparses_changed_file(self, tmp_path):
        from phases.sqlite_graph import GraphDB
        f_a = _ts(tmp_path, "src/a.ts", "export function foo() {}")
        f_b = _ts(tmp_path, "src/b.ts", "export function bar() {}")
        db = GraphDB(str(tmp_path / "g.db"))
        db.build(str(tmp_path))

        hash_before = db.get_file_hash(str(f_b.resolve()))
        f_a.write_text("export function foo() { return 1; }")
        db.update(str(tmp_path))

        assert db.get_file_hash(str(f_b.resolve())) == hash_before
        new_hash = hashlib.sha256("export function foo() { return 1; }".encode()).hexdigest()
        assert db.get_file_hash(str(f_a.resolve())) == new_hash

    def test_update_removes_deleted_file_nodes(self, tmp_path):
        from phases.sqlite_graph import GraphDB
        f_a = _ts(tmp_path, "src/a.ts", "export function foo() {}")
        _ts(tmp_path, "src/b.ts", "export function bar() {}")
        db = GraphDB(str(tmp_path / "g.db"))
        db.build(str(tmp_path))
        assert str(f_a.resolve()) in db.get_all_files()

        f_a.unlink()
        db.update(str(tmp_path))
        assert str(f_a.resolve()) not in db.get_all_files()

    def test_is_fresh_false_after_file_change(self, tmp_path):
        from phases.sqlite_graph import GraphDB
        f = _ts(tmp_path, "src/a.ts", "export function foo() {}")
        db = GraphDB(str(tmp_path / "g.db"))
        db.build(str(tmp_path))
        assert db.is_fresh(str(tmp_path)) is True

        f.write_text("export function foo() { return 42; }")
        assert db.is_fresh(str(tmp_path)) is False

    def test_is_fresh_true_when_nothing_changed(self, tmp_path):
        from phases.sqlite_graph import GraphDB
        _ts(tmp_path, "src/a.ts", "export function foo() {}")
        db = GraphDB(str(tmp_path / "g.db"))
        db.build(str(tmp_path))
        assert db.is_fresh(str(tmp_path)) is True


class TestSchemaVersion:
    def test_schema_version_stored(self, tmp_path):
        from phases.sqlite_graph import GraphDB, SCHEMA_VERSION
        db = GraphDB(str(tmp_path / "g.db"))
        db.build(str(tmp_path))
        assert db.get_schema_version() == SCHEMA_VERSION

    def test_version_mismatch_triggers_rebuild(self, tmp_path):
        from phases.sqlite_graph import GraphDB, SCHEMA_VERSION
        import sqlite3
        _ts(tmp_path, "src/a.ts", "export function foo() {}")
        db_path = str(tmp_path / "g.db")
        db = GraphDB(db_path)
        db.build(str(tmp_path))

        conn = sqlite3.connect(db_path)
        conn.execute("UPDATE schema_version SET version = 0")
        conn.commit()
        conn.close()

        db2 = GraphDB(db_path)
        db2.build(str(tmp_path))
        assert db2.get_schema_version() == SCHEMA_VERSION
        assert any("a.ts" in f for f in db2.get_all_files())


class TestIntegration:
    def test_build_graph_populates_importers(self, tmp_path):
        from phases.context_graph import build_graph
        (tmp_path / "src").mkdir()
        (tmp_path / "src/a.ts").write_text("export function foo() {}")
        (tmp_path / "src/b.ts").write_text("import { foo } from './a'")
        graph = build_graph(str(tmp_path))
        assert "src/b.ts" in graph.find_usages("src/a.ts")

    def test_build_graph_second_run_consistent(self, tmp_path):
        from phases.context_graph import build_graph
        (tmp_path / "src").mkdir()
        (tmp_path / "src/a.ts").write_text("export function foo() {}")
        (tmp_path / "src/b.ts").write_text("import { foo } from './a'")
        r1 = build_graph(str(tmp_path)).find_usages("src/a.ts")
        r2 = build_graph(str(tmp_path)).find_usages("src/a.ts")
        assert r1 == r2

    def test_propagate_risk_works_with_sqlite_graph(self, tmp_path):
        from phases.context_graph import build_graph
        from phases.risk_propagation import propagate_risk
        from phases.symbol_locator import ChangedSymbol
        (tmp_path / "src").mkdir()
        (tmp_path / "src/auth.ts").write_text("export function login() {}")
        (tmp_path / "src/router.ts").write_text("import { login } from './auth'")
        graph = build_graph(str(tmp_path))
        syms = [ChangedSymbol(file="src/auth.ts", symbol="login",
                              symbol_type="function", start_line=1, change_type="modified")]
        paths = propagate_risk(syms, graph)
        impacted = {n for p in paths for n in p.path}
        assert any("router" in n for n in impacted)

    def test_deleted_file_leaves_no_stale_edges(self, tmp_path):
        """Bug 2 fix: deleting an imported file must remove all its edges."""
        from phases.context_graph import build_graph
        (tmp_path / "src").mkdir()
        f_b = tmp_path / "src/b.ts"
        f_b.write_text("export function bar() {}")
        (tmp_path / "src/a.ts").write_text("import { bar } from './b'")

        build_graph(str(tmp_path))  # first run: a imports b
        f_b.unlink()                # delete b.ts
        graph = build_graph(str(tmp_path))  # second run

        # No edge should reference the deleted b.ts
        all_edges = [(e.source, e.target) for e in graph.edges]
        assert not any("b.ts" in s or "b.ts" in t for s, t in all_edges)
        assert "src/b.ts" not in graph._importers

    def test_sqlite_error_falls_back_to_full_parse(self, tmp_path):
        """Bug 3 fix: corrupted DB must not crash build_graph."""
        (tmp_path / "src").mkdir()
        (tmp_path / "src/a.ts").write_text("export function foo() {}")
        # Write a corrupted DB file
        db_dir = tmp_path / ".luna" / "cache"
        db_dir.mkdir(parents=True)
        (db_dir / "context-graph.db").write_text("this is not a valid sqlite db")

        # Should not raise — falls back to full parse
        from phases.context_graph import build_graph
        graph = build_graph(str(tmp_path))
        assert "src/a.ts" in graph.nodes

    def test_bfs_results_identical_across_runs(self, tmp_path):
        from phases.context_graph import build_graph
        from phases.risk_propagation import propagate_risk
        from phases.symbol_locator import ChangedSymbol
        (tmp_path / "src").mkdir()
        (tmp_path / "src/a.ts").write_text("export function foo() {}")
        (tmp_path / "src/b.ts").write_text("import { foo } from './a'")
        (tmp_path / "src/c.ts").write_text("import { foo } from './b'")

        def _run():
            g = build_graph(str(tmp_path))
            s = [ChangedSymbol(file="src/a.ts", symbol="foo",
                               symbol_type="function", start_line=1, change_type="modified")]
            return {n for p in propagate_risk(s, g) for n in p.path}

        assert _run() == _run()
