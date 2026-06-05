# tests/test_context_graph.py
import tempfile
from pathlib import Path
from phases.context_graph import build_graph, save_graph, load_graph


def _make_files(tmp_dir: str, files: dict[str, str]) -> None:
    for name, content in files.items():
        p = Path(tmp_dir) / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")


def test_graph_finds_import_edge():
    with tempfile.TemporaryDirectory() as d:
        _make_files(d, {
            "src/a.js": "import { foo } from './b'\nexport function bar() {}\n",
            "src/b.js": "export function foo() {}\n",
        })
        graph = build_graph(d)
        assert any(
            e.source == "src/a.js" and e.target == "src/b.js" and e.edge_type == "imports"
            for e in graph.edges
        )


def test_graph_finds_export_symbol_node():
    with tempfile.TemporaryDirectory() as d:
        _make_files(d, {
            "src/utils.ts": "export function formatDate(d: Date): string { return '' }\n",
        })
        graph = build_graph(d)
        assert any("formatDate" in nid for nid in graph.nodes)


def test_graph_skips_node_modules():
    with tempfile.TemporaryDirectory() as d:
        _make_files(d, {
            "src/a.js": "import x from 'lodash'\n",
            "node_modules/lodash/index.js": "module.exports = {}\n",
        })
        graph = build_graph(d)
        assert not any("node_modules" in nid for nid in graph.nodes)


def test_find_usages_returns_importing_files():
    with tempfile.TemporaryDirectory() as d:
        _make_files(d, {
            "src/a.js": "import { foo } from './b'\nfoo()\n",
            "src/b.js": "export function foo() {}\n",
        })
        graph = build_graph(d)
        usages = graph.find_usages("src/b.js")
        assert "src/a.js" in usages


def test_graph_resolves_ts_extension():
    with tempfile.TemporaryDirectory() as d:
        _make_files(d, {
            "src/a.ts": "import { x } from './b'\n",
            "src/b.ts": "export const x = 1\n",
        })
        graph = build_graph(d)
        assert any(
            e.source == "src/a.ts" and e.target == "src/b.ts"
            for e in graph.edges
        )


def test_save_and_load_roundtrip():
    with tempfile.TemporaryDirectory() as d:
        _make_files(d, {
            "src/a.js": "import { foo } from './b'\n",
            "src/b.js": "export function foo() {}\n",
        })
        graph = build_graph(d)
        cache_path = Path(d) / "graph.json"
        save_graph(graph, str(cache_path))

        loaded = load_graph(str(cache_path))
        assert loaded is not None
        assert set(loaded.nodes.keys()) == set(graph.nodes.keys())
        assert len(loaded.edges) == len(graph.edges)


def test_load_graph_returns_none_for_missing_file():
    result = load_graph("/nonexistent/path.json")
    assert result is None


def test_loaded_graph_preserves_importers():
    with tempfile.TemporaryDirectory() as d:
        _make_files(d, {
            "src/a.js": "import { foo } from './b'\n",
            "src/b.js": "export function foo() {}\n",
        })
        graph = build_graph(d)
        cache_path = Path(d) / "graph.json"
        save_graph(graph, str(cache_path))

        loaded = load_graph(str(cache_path))
        assert loaded is not None
        assert "src/a.js" in loaded.find_usages("src/b.js")
