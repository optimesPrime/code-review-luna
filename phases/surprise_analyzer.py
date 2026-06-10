"""Surprise Scoring — detects structurally anomalous edges in impact paths.

An edge is "surprising" when it crosses boundaries (module, language, test/non-test)
or exhibits structural anomalies (peripheral node calling a hub).

Scoring rules (additive, capped implicitly by the sum of all rules):
  +0.30  cross-module   — different community; falls back to different os.path.dirname
  +0.20  cross-language — different file extensions
  +0.20  edge-to-hub    — src degree <= 2 AND tgt degree >= median_degree * 3
  +0.15  test boundary  — is_test differs between source and target
  +0.15  bad edge type  — edge_type=="CALLS" and source_kind=="Type"
"""
from __future__ import annotations

import os
import statistics
from dataclasses import dataclass, field


@dataclass
class SurpriseEdge:
    source: str          # source file path
    target: str          # target file path
    score: float
    reasons: list[str] = field(default_factory=list)
    is_suspicious: bool = False


def compute_surprise_score(
    source_file: str,
    target_file: str,
    edge_type: str,
    graph_context: dict,  # {file: {"community": str, "language": str, "degree": int, "is_test": bool}}
    source_kind: str = "",
) -> tuple[float, list[str]]:
    """Compute the surprise score for a single directed edge.

    Parameters
    ----------
    source_file:   path of the calling/importing file
    target_file:   path of the called/imported file
    edge_type:     e.g. "CALLS", "IMPORTS"
    graph_context: per-file metadata dict (missing files are handled gracefully)
    source_kind:   AST node kind for the source symbol, e.g. "Type", "Function"

    Returns
    -------
    (score, reasons)  where reasons is a list of human-readable strings
    """
    score = 0.0
    reasons: list[str] = []

    src_meta = graph_context.get(source_file, {})
    tgt_meta = graph_context.get(target_file, {})

    # --- Rule 1: Cross-module (+0.30) ---
    src_community = src_meta.get("community")
    tgt_community = tgt_meta.get("community")

    if src_community is not None and tgt_community is not None:
        if src_community != tgt_community:
            score += 0.30
            reasons.append(
                f"cross-community: {src_community!r} → {tgt_community!r}"
            )
    else:
        # Fall back to directory comparison
        src_dir = os.path.dirname(source_file)
        tgt_dir = os.path.dirname(target_file)
        if src_dir != tgt_dir:
            score += 0.30
            reasons.append(
                f"cross-module (dir): {src_dir!r} → {tgt_dir!r}"
            )

    # --- Rule 2: Cross-language (+0.20) ---
    src_ext = os.path.splitext(source_file)[1].lower()
    tgt_ext = os.path.splitext(target_file)[1].lower()
    if src_ext and tgt_ext and src_ext != tgt_ext:
        score += 0.20
        reasons.append(f"cross-language: {src_ext} → {tgt_ext}")

    # --- Rule 3: Edge-to-hub (+0.20) ---
    src_degree = src_meta.get("degree")
    tgt_degree = tgt_meta.get("degree")
    if src_degree is not None and tgt_degree is not None:
        # Compute median degree from all nodes in context that have degree info
        degrees = [
            v["degree"]
            for v in graph_context.values()
            if isinstance(v.get("degree"), (int, float))
        ]
        if len(degrees) >= 2:
            median_deg = statistics.median(degrees)
            if src_degree <= 2 and tgt_degree >= median_deg * 3:
                score += 0.20
                reasons.append(
                    f"edge-to-hub: src_degree={src_degree}, tgt_degree={tgt_degree}, "
                    f"median={median_deg}"
                )

    # --- Rule 4: Test boundary (+0.15) ---
    src_is_test = src_meta.get("is_test", False)
    tgt_is_test = tgt_meta.get("is_test", False)
    if src_is_test != tgt_is_test:
        score += 0.15
        reasons.append(
            f"cross-test-boundary: source is_test={src_is_test}, target is_test={tgt_is_test}"
        )

    # --- Rule 5: Bad edge type (+0.15) ---
    if edge_type == "CALLS" and source_kind == "Type":
        score += 0.15
        reasons.append(
            f"bad-edge-type: CALLS from a Type node ({source_file!r})"
        )

    return round(score, 4), reasons


def find_surprising_edges(
    impact_paths: list[list[str]],
    graph_context: dict,
    threshold: float = 0.35,
) -> list[SurpriseEdge]:
    """Extract all edges from impact paths, score them, return those above threshold.

    Parameters
    ----------
    impact_paths: list of file-path lists; adjacent pairs form directed edges
    graph_context: per-file metadata (see compute_surprise_score)
    threshold: score cutoff; edges with score > threshold are marked is_suspicious

    Returns
    -------
    List of SurpriseEdge objects whose score > threshold, deduplicated by (source, target).
    """
    seen: dict[tuple[str, str], SurpriseEdge] = {}

    for path in impact_paths:
        for i in range(len(path) - 1):
            src = path[i]
            tgt = path[i + 1]
            key = (src, tgt)
            if key in seen:
                continue  # deduplicate

            score, reasons = compute_surprise_score(
                src, tgt, edge_type="CALLS", graph_context=graph_context
            )
            if score > threshold:
                seen[key] = SurpriseEdge(
                    source=src,
                    target=tgt,
                    score=score,
                    reasons=reasons,
                    is_suspicious=True,
                )

    return list(seen.values())
