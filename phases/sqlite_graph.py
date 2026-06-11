from __future__ import annotations
import hashlib
import sqlite3
from pathlib import Path

_CURRENT_VERSION = 1

_SKIP_DIRS = {"node_modules", ".git", "dist", "build", "__pycache__", ".cache", ".luna"}
_SOURCE_EXTS = {".js", ".ts", ".jsx", ".tsx", ".vue"}

_DDL = """
CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL);

CREATE TABLE IF NOT EXISTS nodes (
    id          TEXT PRIMARY KEY,
    node_type   TEXT NOT NULL,
    file        TEXT NOT NULL,
    name        TEXT NOT NULL,
    line        INTEGER DEFAULT 0,
    language    TEXT DEFAULT '',
    file_hash   TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS edges (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    source      TEXT NOT NULL,
    target      TEXT NOT NULL,
    edge_type   TEXT NOT NULL,
    file        TEXT NOT NULL,
    line        INTEGER DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_edges_source ON edges(source);
CREATE INDEX IF NOT EXISTS idx_edges_target ON edges(target);

CREATE VIRTUAL TABLE IF NOT EXISTS nodes_fts USING fts5(
    name, node_type, file,
    content=nodes, content_rowid=rowid,
    tokenize='porter unicode61'
);
"""


def _file_hash(path: Path) -> str:
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()


def _ext_language(ext: str) -> str:
    return {".ts": "typescript", ".tsx": "typescript",
            ".js": "javascript", ".vue": "vue"}.get(ext, "javascript")


class GraphDB:
    def __init__(self, db_path: str) -> None:
        self._path = db_path
        self._conn = sqlite3.connect(db_path)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._init_schema()

    def _init_schema(self) -> None:
        for stmt in _DDL.strip().split(";"):
            s = stmt.strip()
            if s:
                self._conn.execute(s)
        row = self._conn.execute("SELECT version FROM schema_version").fetchone()
        if row is None:
            self._conn.execute("INSERT INTO schema_version VALUES (?)", (_CURRENT_VERSION,))
            self._conn.commit()
        elif row[0] != _CURRENT_VERSION:
            self._rebuild_schema()

    def _rebuild_schema(self) -> None:
        tables = [r[0] for r in self._conn.execute(
            "SELECT name FROM sqlite_master WHERE type IN ('table','index') AND name NOT LIKE 'sqlite_%'"
        ).fetchall()]
        with self._conn:
            for t in tables:
                self._conn.execute(f"DROP TABLE IF EXISTS [{t}]")
            for stmt in _DDL.strip().split(";"):
                s = stmt.strip()
                if s:
                    self._conn.execute(s)
            self._conn.execute("INSERT INTO schema_version VALUES (?)", (_CURRENT_VERSION,))

    # ------------------------------------------------------------------
    # build
    # ------------------------------------------------------------------

    def build(self, project_root: str) -> None:
        root = Path(project_root)
        source_files = [
            p for p in root.rglob("*")
            if p.suffix in _SOURCE_EXTS
            and not any(part in _SKIP_DIRS for part in p.relative_to(root).parts)
        ]
        existing_hashes: dict[str, str] = {
            r[0]: r[1]
            for r in self._conn.execute("SELECT file, file_hash FROM nodes WHERE node_type='file'").fetchall()
        }
        with self._conn:
            for src in source_files:
                rel = str(src.relative_to(root))
                fhash = _file_hash(src)
                if existing_hashes.get(rel) == fhash:
                    continue
                self._parse_and_insert(src, rel, root, fhash)

    def _parse_and_insert(self, src: Path, rel: str, root: Path, fhash: str) -> None:
        # Remove stale data for this file
        self._conn.execute("DELETE FROM nodes WHERE file=?", (rel,))
        self._conn.execute("DELETE FROM edges WHERE file=?", (rel,))

        lang = _ext_language(src.suffix.lower())

        # Insert file node with empty hash until parse succeeds.
        # This prevents a failed parse from persisting the real hash,
        # which would cause update() to skip the file on future runs.
        self._conn.execute(
            "INSERT OR REPLACE INTO nodes(id, node_type, file, name, line, language, file_hash) VALUES (?,?,?,?,?,?,?)",
            (rel, "file", rel, rel, 0, lang, ""),
        )

        # Parse exports + imports using existing context_graph helpers
        from phases.context_graph import ContextGraph, _process_js_file, _process_vue_file, _process_file_regex
        tmp_graph = ContextGraph()
        parse_ok = False
        try:
            if src.suffix == ".vue":
                _process_vue_file(src, rel, root, tmp_graph)
            else:
                _process_js_file(src, rel, root, tmp_graph)
            parse_ok = True
        except Exception:
            try:
                _process_file_regex(src, rel, root, tmp_graph)
                parse_ok = True
            except Exception:
                pass

        for node in tmp_graph.nodes.values():
            if node.id == rel:
                continue  # file node already inserted
            self._conn.execute(
                "INSERT OR REPLACE INTO nodes(id, node_type, file, name, line, language, file_hash) VALUES (?,?,?,?,?,?,?)",
                (node.id, node.node_type, node.file, node.name, node.line, lang, ""),
            )
        for edge in tmp_graph.edges:
            self._conn.execute(
                "INSERT INTO edges(source, target, edge_type, file) VALUES (?,?,?,?)",
                (edge.source, edge.target, edge.edge_type, rel),
            )

        # Only stamp the real hash after a successful parse.
        # An empty hash causes update() to reparse on next run, enabling retry.
        if parse_ok:
            self._conn.execute(
                "UPDATE nodes SET file_hash=? WHERE id=? AND node_type='file'",
                (fhash, rel),
            )

    # ------------------------------------------------------------------
    # update (Task 2)
    # ------------------------------------------------------------------

    def update(self, project_root: str) -> None:
        root = Path(project_root)
        source_files = {
            str(p.relative_to(root)): p
            for p in root.rglob("*")
            if p.suffix in _SOURCE_EXTS
            and not any(part in _SKIP_DIRS for part in p.relative_to(root).parts)
        }
        stored_hashes: dict[str, str] = {
            r[0]: r[1]
            for r in self._conn.execute("SELECT file, file_hash FROM nodes WHERE node_type='file'").fetchall()
        }
        with self._conn:
            # Removed files
            for stored_file in list(stored_hashes):
                if stored_file not in source_files:
                    self._conn.execute("DELETE FROM nodes WHERE file=?", (stored_file,))
                    self._conn.execute("DELETE FROM edges WHERE file=?", (stored_file,))
            # New or changed files
            for rel, src in source_files.items():
                fhash = _file_hash(src)
                if stored_hashes.get(rel) == fhash:
                    continue
                self._parse_and_insert(src, rel, root, fhash)

    def is_fresh(self, project_root: str) -> bool:
        root = Path(project_root)
        source_files = [
            p for p in root.rglob("*")
            if p.suffix in _SOURCE_EXTS
            and not any(part in _SKIP_DIRS for part in p.relative_to(root).parts)
        ]
        stored_hashes: dict[str, str] = {
            r[0]: r[1]
            for r in self._conn.execute("SELECT file, file_hash FROM nodes WHERE node_type='file'").fetchall()
        }
        if len(source_files) != len(stored_hashes):
            return False
        for src in source_files:
            rel = str(src.relative_to(root))
            if stored_hashes.get(rel) != _file_hash(src):
                return False
        return True

    # ------------------------------------------------------------------
    # BFS (Task 3)
    # ------------------------------------------------------------------

    def bfs_impact(self, seeds: list[str], max_depth: int = 3) -> list[dict]:
        if not seeds:
            return []
        placeholders = ",".join("?" * len(seeds))
        sql = f"""
        WITH RECURSIVE impact(node, depth, path) AS (
            SELECT target, 1, target
            FROM edges
            WHERE source IN ({placeholders})
            AND edge_type = 'imports'
            UNION ALL
            SELECT e.target, i.depth + 1, i.path || ',' || e.target
            FROM edges e
            JOIN impact i ON e.source = i.node
            WHERE i.depth < ?
            AND instr(',' || i.path || ',', ',' || e.target || ',') = 0
        )
        SELECT DISTINCT node, depth FROM impact ORDER BY depth
        """
        rows = self._conn.execute(sql, seeds + [max_depth]).fetchall()
        return [{"node": r[0], "depth": r[1]} for r in rows]

    def get_importers(self, file: str) -> list[str]:
        rows = self._conn.execute(
            "SELECT DISTINCT source FROM edges WHERE target=? AND edge_type='imports'",
            (file,),
        ).fetchall()
        return [r[0] for r in rows]

    # ------------------------------------------------------------------
    # FTS search (for hybrid-search-rrf)
    # ------------------------------------------------------------------

    def all_nodes(self) -> list[tuple]:
        return self._conn.execute(
            "SELECT id, node_type, file, name, line FROM nodes"
        ).fetchall()

    def all_edges(self) -> list[tuple]:
        return self._conn.execute(
            "SELECT source, target, edge_type FROM edges"
        ).fetchall()

    def import_edges(self) -> list[tuple]:
        return self._conn.execute(
            "SELECT source, target FROM edges WHERE edge_type='imports'"
        ).fetchall()

    def fts_search(self, query: str, limit: int = 20) -> list[dict]:
        try:
            rows = self._conn.execute(
                "SELECT name, node_type, file FROM nodes_fts WHERE nodes_fts MATCH ? ORDER BY rank LIMIT ?",
                (query, limit),
            ).fetchall()
            return [{"name": r[0], "node_type": r[1], "file": r[2]} for r in rows]
        except sqlite3.OperationalError:
            return []

    def close(self) -> None:
        self._conn.close()
