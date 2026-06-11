from __future__ import annotations
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from phases.sqlite_graph import GraphDB
    from phases.symbol_locator import ChangedSymbol
    from phases.risk_propagation import ImpactPath


@dataclass
class HybridResult:
    file: str
    score: float
    sources: list[str]  # ["bfs", "fts"] 表示来自哪路检索


# ---------------------------------------------------------------------------
# FTS5 搜索
# ---------------------------------------------------------------------------

def fts_search(db: "GraphDB", query: str, limit: int = 20) -> list[str]:
    """用 FTS5 搜索包含 query 的文件，返回文件路径列表（按相关度排序）。"""
    rows = db.fts_search(query, limit=limit)
    seen: dict[str, None] = {}
    for r in rows:
        seen[r["file"]] = None
    return list(seen.keys())


# ---------------------------------------------------------------------------
# RRF 合并
# ---------------------------------------------------------------------------

def rrf_merge(result_lists: list[list[str]], k: int = 60) -> list[tuple[str, float]]:
    """Reciprocal Rank Fusion — 合并多路排序结果，不需要归一化。"""
    scores: dict[str, float] = {}
    for results in result_lists:
        for rank, item in enumerate(results):
            scores[item] = scores.get(item, 0.0) + 1.0 / (k + rank + 1)
    return sorted(scores.items(), key=lambda x: x[1], reverse=True)


# ---------------------------------------------------------------------------
# Query Kind Boosting
# ---------------------------------------------------------------------------

def detect_query_kind_boost(symbol: str) -> dict[str, float]:
    """根据符号名格式推断类型，返回对应的排序加权。"""
    if not symbol:
        return {}
    if symbol[0].isupper() and "_" not in symbol:   # PascalCase → 可能是 Class/Component
        return {"class": 1.5, "component": 1.3}
    if "_" in symbol.lower() and not symbol[0].isupper():  # snake_case → Function
        return {"function": 1.5}
    if "." in symbol:                                # qualified name
        return {"qualified": 2.0}
    return {}


# ---------------------------------------------------------------------------
# hybrid_search 主函数
# ---------------------------------------------------------------------------

def hybrid_search(
    db: "GraphDB",
    symbols: list["ChangedSymbol"],
    max_depth: int = 3,
    fts_limit: int = 20,
    project_root: str = ".",
) -> list[HybridResult]:
    """
    三路检索合并：
      路径 1 (BFS)  — 依赖边传播，找直接/间接 importer
      路径 2 (FTS5) — 全文搜索符号名，找「引用但不 import」的文件
    结果用 RRF 合并，再按 query kind 加权排序。
    """
    if not symbols:
        return []

    seeds = [s.file for s in symbols]

    # 路径 1：BFS
    bfs_rows = db.bfs_impact(seeds, max_depth=max_depth)
    bfs_files = [r["node"] for r in bfs_rows]

    # 路径 2：内容 grep（找「引用但不 import」的文件，如测试文件、注释引用）
    # 同时用 FTS5 找同名导出符号（捕捉接口漂移、重名函数）
    fts_files_all: list[str] = []
    from phases.caller_context import grep_call_sites
    import os
    ignore_dirs = [".git", "node_modules", "dist", "build", "__pycache__", ".luna"]
    for sym in symbols:
        # grep：找实际引用该符号名的文件
        grep_hits = grep_call_sites(sym.symbol, project_root, ignore_dirs, self_file=sym.file)
        for file_path, _ in grep_hits:
            rel = os.path.relpath(file_path, project_root)
            if rel not in seeds and rel not in fts_files_all:
                fts_files_all.append(rel)
        # FTS5：找导出同名符号的文件（不同模块里的同名函数）
        for r in fts_search(db, sym.symbol, limit=fts_limit):
            if r not in seeds and r not in fts_files_all:
                fts_files_all.append(r)

    # RRF 合并
    merged = rrf_merge([bfs_files, fts_files_all])

    # Query kind boosting：对 FTS5 命中的符号应用类型加权
    boost_map: dict[str, float] = {}
    for sym in symbols:
        boost = detect_query_kind_boost(sym.symbol)
        for node_type, weight in boost.items():
            boost_map[node_type] = max(boost_map.get(node_type, 1.0), weight)

    bfs_set = set(bfs_files)
    fts_set = set(fts_files_all)

    results: list[HybridResult] = []
    for file, score in merged:
        if file in seeds:
            continue
        sources = []
        if file in bfs_set:
            sources.append("bfs")
        if file in fts_set:
            sources.append("fts")
        results.append(HybridResult(file=file, score=score, sources=sources))

    return results


# ---------------------------------------------------------------------------
# 接入 context_pack：把 hybrid_search 新发现的文件注入 impact_paths
# ---------------------------------------------------------------------------

def augment_impact_paths(
    existing_paths: list["ImpactPath"],
    db: "GraphDB",
    symbols: list["ChangedSymbol"],
    project_root: str = ".",
) -> list["ImpactPath"]:
    """
    在现有 BFS impact_paths 基础上，追加 hybrid_search 找到的额外文件。
    已在 existing_paths 里出现的文件不重复添加。
    新增的条目标注 evidence="语义相关（FTS5/grep，非直接依赖）"，confidence=low。
    """
    from phases.risk_propagation import ImpactPath

    existing_files = {p.path[-1] for p in existing_paths}
    extra = hybrid_search(db, symbols, project_root=project_root)

    result = list(existing_paths)
    for hit in extra:
        if hit.file not in existing_files:
            result.append(ImpactPath(
                path=[hit.file],
                risk="low",
                confidence="low",
                evidence=f"语义相关（{'、'.join(hit.sources)}，非直接依赖）",
                needs_human_review=False,
            ))
            existing_files.add(hit.file)
    return result
