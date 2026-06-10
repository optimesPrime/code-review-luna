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
    median_degree: float | None = None,
) -> tuple[float, list[str]]:
    """Compute the surprise score for a single directed edge.

    Parameters
    ----------
    source_file:   path of the calling/importing file
    target_file:   path of the called/imported file
    edge_type:     e.g. "CALLS", "IMPORTS"
    graph_context: per-file metadata dict (missing files are handled gracefully)
    source_kind:   AST node kind for the source symbol, e.g. "Type", "Function"
    median_degree: pre-computed median degree to avoid O(N) recomputation per edge;
                   if None, computed lazily from graph_context (for standalone calls)

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
        # Use pre-computed median_degree when available (avoids O(N) recomputation per edge);
        # fall back to computing from graph_context when called standalone.
        if median_degree is not None:
            median_deg: float | None = median_degree
        else:
            degrees = [
                v["degree"]
                for v in graph_context.values()
                if isinstance(v.get("degree"), (int, float))
            ]
            median_deg = statistics.median(degrees) if len(degrees) >= 2 else None

        if median_deg is not None and src_degree <= 2 and tgt_degree >= median_deg * 3:
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
    threshold: score cutoff; edges with score >= threshold are marked is_suspicious

    Returns
    -------
    List of SurpriseEdge objects whose score >= threshold, deduplicated by (source, target).
    """
    seen: dict[tuple[str, str], SurpriseEdge] = {}

    # Pre-compute median degree once — avoids O(E*N) recalculation inside the loop.
    degrees = [v["degree"] for v in graph_context.values() if isinstance(v.get("degree"), (int, float))]
    median_deg: float | None = statistics.median(degrees) if len(degrees) >= 2 else None

    for path in impact_paths:
        for i in range(len(path) - 1):
            src = path[i]
            tgt = path[i + 1]
            key = (src, tgt)
            if key in seen:
                continue  # deduplicate

            # impact_paths 只含文件路径，无 edge metadata，统一视为 CALLS 边
            score, reasons = compute_surprise_score(
                src, tgt, edge_type="CALLS", graph_context=graph_context,
                median_degree=median_deg,
            )
            if score >= threshold:
                seen[key] = SurpriseEdge(
                    source=src,
                    target=tgt,
                    score=score,
                    reasons=reasons,
                    is_suspicious=True,
                )

    return list(seen.values())


def find_untested_hotspots(
    changed_symbols: list[dict],   # 每个 dict 有 "symbol", "degree", "is_test" 字段
    related_tests: list[str],       # 测试文件路径列表（非空即有覆盖）
    min_degree: int = 5,
) -> list[str]:
    """找出高度数但无测试覆盖的符号。

    条件：degree >= min_degree 且 related_tests 为空 且 is_test=False。
    返回符号名列表。

    设计说明：related_tests 非空时，整体视为有覆盖，直接返回空列表。
    这是有意设计（非 bug）：图分析已在上游完成符号级关联，此处只做粗粒度判断。

    注意：is_test 字段缺失时保守处理——跳过该符号，不纳入热点，
    以避免将未知状态的测试文件误判为非测试代码。
    """
    if related_tests:
        return []
    return [
        sym["symbol"]
        for sym in changed_symbols
        if not sym.get("is_test", True) and sym.get("degree", 0) >= min_degree
    ]


def find_bridge_nodes_in_impact(
    impact_paths: list[list[str]],  # 每条路径是文件/节点名列表
) -> list[str]:
    """找出影响路径中的桥接节点。

    简化定义：出现在 >= 2 条不同路径上的中间节点（非首尾节点）即为桥接节点。
    去重后返回节点名列表。

    注意：
    - 路径长度 < 3 的路径将被整体忽略（中间节点为空，path[1:-1] 结果为空列表）。
    - 返回列表顺序未保证（取决于 dict 迭代顺序，仅保证 Python 3.7+ 插入顺序）。
    """

    middle_node_paths: dict[str, set[int]] = {}
    for path_idx, path in enumerate(impact_paths):
        # 中间节点：排除第一个和最后一个
        for node in path[1:-1]:
            middle_node_paths.setdefault(node, set()).add(path_idx)

    return [node for node, path_indices in middle_node_paths.items() if len(path_indices) >= 2]


def generate_review_questions(
    surprise_edges: list[SurpriseEdge],
    hotspots: list[str],           # 无测试的高频符号名
    bridges: list[str],            # 桥接节点文件/节点名
    max_questions: int = 7,
) -> list[str]:
    """把图分析信号翻译成中文审查问题。

    优先级：surprise_score 高的边 → 无测试热点 → 桥接节点
    每类最多 3 个，总上限 max_questions 个
    问题用中文，包含具体文件名和行为描述
    """
    questions: list[str] = []

    # 1. Surprise edges（按 score 降序，最多 3 个）
    sorted_edges = sorted(surprise_edges, key=lambda e: e.score, reverse=True)
    for edge in sorted_edges[:3]:
        if len(questions) >= max_questions:
            break
        src = edge.source.split("/")[-1]  # 只取文件名
        tgt = edge.target.split("/")[-1]
        reasons_str = "、".join(edge.reasons)
        questions.append(
            f"{src} 调用了 {tgt}（{reasons_str}），这个跨边界依赖是有意的吗？"
        )

    # 2. Untested hotspots（最多 3 个）
    for sym in hotspots[:3]:
        if len(questions) >= max_questions:
            break
        questions.append(f"{sym} 有多个调用者但没有测试覆盖，是否需要补充测试？")

    # 3. Bridge nodes（最多 3 个）
    for node in bridges[:3]:
        if len(questions) >= max_questions:
            break
        name = node.split("/")[-1]
        questions.append(f"{name} 是多个模块的关键连接器，这次改动是否会影响其他模块？")

    return questions[:max_questions]
