"""Tests for phases/surprise_analyzer.py — Surprise Scoring logic.

TDD: these tests are written first and must fail before implementation exists.
"""
from __future__ import annotations

import pytest

from phases.surprise_analyzer import (
    SurpriseEdge,
    compute_surprise_score,
    find_surprising_edges,
    find_untested_hotspots,
    find_bridge_nodes_in_impact,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ctx(**overrides):
    """Build a minimal graph_context entry."""
    defaults = {"community": None, "language": "python", "degree": 5, "is_test": False}
    defaults.update(overrides)
    return defaults


# ---------------------------------------------------------------------------
# compute_surprise_score tests
# ---------------------------------------------------------------------------


def test_cross_module_edge_scores_high():
    """Edge crossing different directories (no community info) must score > 0.3."""
    source = "module_a/foo.py"
    target = "module_b/bar.py"
    graph_context = {
        source: _ctx(),
        target: _ctx(),
    }
    score, reasons = compute_surprise_score(source, target, "CALLS", graph_context)
    assert score >= 0.3, f"Expected score >= 0.3 for cross-module edge, got {score}"
    assert any("module" in r.lower() or "community" in r.lower() or "cross" in r.lower() for r in reasons), \
        f"Expected a cross-module reason, got: {reasons}"


def test_same_module_edge_scores_low():
    """Edge within the same directory must score < 0.1."""
    source = "module_a/foo.py"
    target = "module_a/bar.py"
    graph_context = {
        source: _ctx(),
        target: _ctx(),
    }
    score, reasons = compute_surprise_score(source, target, "IMPORTS", graph_context)
    assert score < 0.1, f"Expected score < 0.1 for same-module edge, got {score}"


def test_cross_language_adds_to_score():
    """.ts file calling .py file must add 0.2 to the score."""
    source = "frontend/App.ts"
    target = "backend/utils.py"
    graph_context = {
        source: _ctx(language="typescript"),
        target: _ctx(language="python"),
    }
    # Both in different dirs (cross-module: +0.30) AND cross-language (+0.20) => 0.50
    score, reasons = compute_surprise_score(source, target, "CALLS", graph_context)
    # Isolate cross-language contribution: put them in the same dir
    source2 = "shared/App.ts"
    target2 = "shared/utils.py"
    graph_context2 = {
        source2: _ctx(language="typescript"),
        target2: _ctx(language="python"),
    }
    score2, reasons2 = compute_surprise_score(source2, target2, "CALLS", graph_context2)
    assert score2 >= 0.2, (
        f"Cross-language edge (same dir) should add at least 0.2 to score, got {score2}"
    )
    assert any("language" in r.lower() or "lang" in r.lower() or "extension" in r.lower() for r in reasons2), \
        f"Expected a cross-language reason, got: {reasons2}"


def test_score_threshold_marks_suspicious():
    """find_surprising_edges must set is_suspicious=True when score > 0.35."""
    # cross-module (+0.30) + cross-language (+0.20) = 0.50 > 0.35
    paths = [["module_a/App.ts", "module_b/server.py"]]
    graph_context = {
        "module_a/App.ts": _ctx(language="typescript"),
        "module_b/server.py": _ctx(language="python"),
    }
    edges = find_surprising_edges(paths, graph_context, threshold=0.35)
    assert len(edges) == 1, f"Expected 1 suspicious edge, got {len(edges)}"
    edge = edges[0]
    assert edge.is_suspicious is True, f"Expected is_suspicious=True, got {edge.is_suspicious}"
    assert edge.score > 0.35, f"Expected score > 0.35, got {edge.score}"


# ---------------------------------------------------------------------------
# Rule 3 / 4 / 5 coverage
# ---------------------------------------------------------------------------


def test_edge_to_hub_rule():
    """src degree=1, tgt degree=30, median=1 → should trigger edge-to-hub (+0.20)."""
    source = "leaf/widget.py"
    target = "core/hub.py"
    # Two nodes: degrees [1, 30] → median = 15.5; 30 >= 15.5*3 = 46.5 → False
    # To guarantee trigger: use three nodes so median stays low.
    # degrees = [1, 1, 30] → median = 1; 30 >= 1*3 = 3 → True
    graph_context = {
        source: _ctx(degree=1),
        target: _ctx(degree=30),
        "other/helper.py": _ctx(degree=1),
    }
    score, reasons = compute_surprise_score(source, target, "CALLS", graph_context)
    assert score >= 0.20, f"Expected score >= 0.20 for edge-to-hub, got {score}"
    assert any("hub" in r.lower() for r in reasons), \
        f"Expected an edge-to-hub reason, got: {reasons}"


def test_cross_test_boundary_adds_score():
    """src is_test=True, tgt is_test=False, edge_type=CALLS → should trigger +0.15."""
    source = "tests/test_widget.py"
    target = "src/widget.py"
    graph_context = {
        source: _ctx(is_test=True),
        target: _ctx(is_test=False),
    }
    score, reasons = compute_surprise_score(source, target, "CALLS", graph_context)
    assert score >= 0.15, f"Expected score >= 0.15 for cross-test-boundary, got {score}"
    assert any("test" in r.lower() for r in reasons), \
        f"Expected a cross-test-boundary reason, got: {reasons}"


def test_bad_edge_type_adds_score():
    """edge_type=CALLS with source_kind=Type → should trigger bad-edge-type (+0.15)."""
    source = "models/user.py"
    target = "services/user_service.py"
    graph_context = {
        source: _ctx(),
        target: _ctx(),
    }
    score, reasons = compute_surprise_score(
        source, target, "CALLS", graph_context, source_kind="Type"
    )
    assert score >= 0.15, f"Expected score >= 0.15 for bad-edge-type, got {score}"
    assert any("bad" in r.lower() or "type" in r.lower() for r in reasons), \
        f"Expected a bad-edge-type reason, got: {reasons}"


# ---------------------------------------------------------------------------
# find_untested_hotspots tests
# ---------------------------------------------------------------------------


def test_hotspot_has_high_degree_no_tests():
    """度数 >= 5，related_tests 为空 → 进热点列表"""
    changed_symbols = [
        {"symbol": "handleSubmit", "degree": 8, "is_test": False}
    ]
    related_tests = []
    result = find_untested_hotspots(changed_symbols, related_tests, min_degree=5)
    assert "handleSubmit" in result


def test_hotspot_with_tests_not_flagged():
    """有测试 → 不进热点列表"""
    changed_symbols = [
        {"symbol": "handleSubmit", "degree": 8, "is_test": False}
    ]
    related_tests = ["tests/test_form.py::test_handle_submit"]
    result = find_untested_hotspots(changed_symbols, related_tests, min_degree=5)
    assert "handleSubmit" not in result


def test_bridge_node_single_connector():
    """出现在 >= 2 条不同路径上的节点 → 进桥接列表"""
    impact_paths = [
        ["a.py", "shared.py", "b.py"],
        ["c.py", "shared.py", "d.py"],
    ]
    result = find_bridge_nodes_in_impact(impact_paths)
    assert "shared.py" in result
