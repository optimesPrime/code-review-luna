from __future__ import annotations

import hashlib
import re
import sqlite3
from pathlib import Path

SCHEMA_VERSION = 1

_SKIP_DIRS = {"node_modules", ".git", "dist", "build", "__pycache__", ".cache", ".luna"}
_SOURCE_EXTS = {".js", ".ts", ".jsx", ".tsx", ".vue"}

_IMPORT_RE = re.compile(
    r"""(?:import\s+(?:[^'"]+\s+from\s+)?|require\s*\(\s*)['"](\.\.?/[^'"]+)['"]"""
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS files (
    path        TEXT PRIMARY KEY,
    file_hash   TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS edges (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    source      TEXT NOT NULL,
    target      TEXT NOT NULL,
    UNIQUE(source, target)
);

CREATE INDEX IF NOT EXISTS idx_edges_source ON edges(source);
CREATE INDEX IF NOT EXISTS idx_edges_target ON edges(target);
"""


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _resolve_import(importer_path: str, import_spec: str, project_root: str) -> str | None:
    importer = Path(importer_path)
    base = (importer.parent / import_spec).resolve()

    candidates = [base] + [base.with_suffix(ext) for ext in _SOURCE_EXTS]
    candidates += [base / f"index{ext}" for ext in _SOURCE_EXTS]

    for candidate in candidates:
        if candidate.exists():
            try:
                return str(candidate)
            except ValueError:
                pass
    return None


def _scan_files(project_root: str) -> list[Path]:
    root = Path(project_root).resolve()
    result = []
    for p in root.rglob("*"):
        if not p.is_file() or p.suffix not in _SOURCE_EXTS:
            continue
        try:
            rel_parts = p.resolve().relative_to(root).parts
        except ValueError:
            continue
        if not any(part in _SKIP_DIRS for part in rel_parts):
            result.append(p.resolve())
    return result


def _parse_imports(file_path: str, content: str) -> list[str]:
    results = []
    for m in _IMPORT_RE.finditer(content):
        spec = m.group(1)
        resolved = _resolve_import(file_path, spec, str(Path(file_path).parent))
        if resolved:
            results.append(resolved)
    return results


class GraphDB:
    """SQLite-backed graph store with SHA-256 incremental caching.

    DB conventions:
      files.path          = absolute path
      edges.source        = absolute path of the imported file (b.ts)
      edges.target        = absolute path of the importer file  (a.ts)
    """

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._conn.executescript(_SCHEMA)
        self._conn.commit()
        self._ensure_schema_version()

    # ── schema version ────────────────────────────────────────────────────────

    def _ensure_schema_version(self) -> None:
        row = self._conn.execute("SELECT version FROM schema_version").fetchone()
        if row is None:
            self._conn.execute("INSERT INTO schema_version VALUES (?)", (SCHEMA_VERSION,))
            self._conn.commit()
        elif row[0] != SCHEMA_VERSION:
            self._rebuild_schema()

    def _rebuild_schema(self) -> None:
        self._conn.executescript("""
            DROP TABLE IF EXISTS edges;
            DROP TABLE IF EXISTS files;
            DROP TABLE IF EXISTS schema_version;
        """)
        self._conn.executescript(_SCHEMA)
        self._conn.execute("INSERT INTO schema_version VALUES (?)", (SCHEMA_VERSION,))
        self._conn.commit()

    def get_schema_version(self) -> int:
        row = self._conn.execute("SELECT version FROM schema_version").fetchone()
        return row[0] if row else -1

    # ── build ─────────────────────────────────────────────────────────────────

    def build(self, project_root: str) -> None:
        files = _scan_files(project_root)
        with self._conn:
            for file_path in files:
                try:
                    content_bytes = file_path.read_bytes()
                except OSError:
                    continue
                new_hash = _sha256(content_bytes)
                if self.get_file_hash(str(file_path)) == new_hash:
                    continue
                self._index_file(str(file_path), content_bytes.decode("utf-8", errors="ignore"), new_hash)

    def _index_file(self, file_path: str, content: str, file_hash: str) -> None:
        self._conn.execute("DELETE FROM edges WHERE target = ?", (file_path,))
        self._conn.execute(
            "INSERT OR REPLACE INTO files(path, file_hash) VALUES (?, ?)",
            (file_path, file_hash),
        )
        for imported in _parse_imports(file_path, content):
            self._conn.execute(
                "INSERT OR IGNORE INTO edges(source, target) VALUES (?, ?)",
                (imported, file_path),
            )

    # ── update (incremental) ─────────────────────────────────────────────────

    def update(self, project_root: str) -> None:
        current_files = {str(p) for p in _scan_files(project_root)}
        stored_files = {row[0] for row in self._conn.execute("SELECT path FROM files")}

        with self._conn:
            for deleted in stored_files - current_files:
                self._conn.execute("DELETE FROM files WHERE path = ?", (deleted,))
                self._conn.execute(
                    "DELETE FROM edges WHERE source = ? OR target = ?", (deleted, deleted)
                )
            for file_path in current_files:
                try:
                    content_bytes = Path(file_path).read_bytes()
                except OSError:
                    continue
                new_hash = _sha256(content_bytes)
                if self.get_file_hash(file_path) == new_hash:
                    continue
                self._index_file(file_path, content_bytes.decode("utf-8", errors="ignore"), new_hash)

    def is_fresh(self, project_root: str) -> bool:
        for file_path in _scan_files(project_root):
            try:
                content_bytes = file_path.read_bytes()
            except OSError:
                return False
            if _sha256(content_bytes) != self.get_file_hash(str(file_path)):
                return False
        return True

    # ── queries ───────────────────────────────────────────────────────────────

    def get_importers(self, file_path: str) -> list[str]:
        rows = self._conn.execute(
            "SELECT target FROM edges WHERE source = ?", (file_path,)
        ).fetchall()
        return [r[0] for r in rows]

    def get_all_files(self) -> list[str]:
        return [r[0] for r in self._conn.execute("SELECT path FROM files").fetchall()]

    def get_all_hashes(self) -> dict[str, str]:
        """Return {abs_path: sha256} for all tracked files."""
        return {
            row[0]: row[1]
            for row in self._conn.execute("SELECT path, file_hash FROM files").fetchall()
        }

    def get_all_edges(self) -> list[tuple[str, str]]:
        """Return [(source_abs, target_abs)] — source=imported, target=importer."""
        return self._conn.execute("SELECT source, target FROM edges").fetchall()

    def sync(
        self,
        deleted: set[str],
        reparsed: set[str],
        new_importers: dict[str, set[str]],
        project_root: str,
    ) -> None:
        """Atomic sync: remove deleted files, update re-parsed files, insert new edges.

        new_importers: {imported_rel: {importer_rel, ...}} from in-memory parse
        project_root: used to convert relative→absolute paths
        """
        import hashlib as _hl
        root = Path(project_root).resolve()

        with self._conn:
            for path in deleted:
                self._conn.execute("DELETE FROM files WHERE path = ?", (path,))
                self._conn.execute(
                    "DELETE FROM edges WHERE source = ? OR target = ?", (path, path)
                )

            for abs_path in reparsed:
                try:
                    new_hash = _hl.sha256(Path(abs_path).read_bytes()).hexdigest()
                except OSError:
                    continue
                self._conn.execute(
                    "INSERT OR REPLACE INTO files(path, file_hash) VALUES (?, ?)",
                    (abs_path, new_hash),
                )
                self._conn.execute("DELETE FROM edges WHERE target = ?", (abs_path,))

            for imported_rel, importers in new_importers.items():
                source_abs = str(root / imported_rel)
                for importer_rel in importers:
                    importer_abs = str(root / importer_rel)
                    if importer_abs in reparsed:
                        self._conn.execute(
                            "INSERT OR IGNORE INTO edges(source, target) VALUES (?, ?)",
                            (source_abs, importer_abs),
                        )

    def get_file_hash(self, file_path: str) -> str:
        row = self._conn.execute(
            "SELECT file_hash FROM files WHERE path = ?", (file_path,)
        ).fetchone()
        return row[0] if row else ""

    def close(self) -> None:
        self._conn.close()
