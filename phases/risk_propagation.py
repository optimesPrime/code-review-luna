# phases/risk_propagation.py
from __future__ import annotations
from collections import deque
from dataclasses import dataclass, field
from phases.symbol_locator import ChangedSymbol
from phases.context_graph import ContextGraph


_HIGH_RISK_KEYWORDS = {
    "auth", "login", "logout", "token", "session", "permission",
    "request", "intercept", "trade", "pay", "order", "header",
    "guard", "route", "middleware",
}

_MAX_DEPTH = 3


@dataclass
class ImpactPath:
    path: list[str]
    risk: str        # "high" | "medium" | "low"
    confidence: str  # "high" | "medium" | "low"
    evidence: str
    needs_human_review: bool = False


def _classify(path: list[str], depth: int) -> tuple[str, str]:
    combined = " ".join(path).lower()
    has_keyword = any(kw in combined for kw in _HIGH_RISK_KEYWORDS)

    if depth <= 1:
        return ("high" if has_keyword else "medium"), "high"
    elif depth == 2:
        return ("high" if has_keyword else "medium"), "medium"
    else:
        return ("medium" if has_keyword else "low"), "low"


def propagate_risk(
    symbols: list[ChangedSymbol],
    graph: ContextGraph,
    max_depth: int = _MAX_DEPTH,
) -> list[ImpactPath]:
    paths: list[ImpactPath] = []

    for symbol in symbols:
        start = symbol.file
        # Emit the origin path
        paths.append(ImpactPath(
            path=[f"{start}:{symbol.symbol}"],
            risk="high" if symbol.change_type == "modified" else "medium",
            confidence="high",
            evidence=f"直接改动：{symbol.symbol}（{symbol.change_type}）",
        ))

        # BFS over importers
        queue: deque[tuple[str, list[str], int]] = deque([(start, [start], 1)])
        visited: set[str] = {start}

        while queue:
            current, current_path, depth = queue.popleft()
            if depth > max_depth:
                continue

            for importer in graph.find_usages(current):
                if importer in visited:
                    continue
                visited.add(importer)

                new_path = current_path + [importer]
                risk, confidence = _classify(new_path, depth)
                needs_review = depth >= 2

                evidence = f"{'→' * depth} {importer} 通过 import 依赖 {current}"
                if any(kw in importer.lower() for kw in _HIGH_RISK_KEYWORDS):
                    evidence += "（高风险关键词）"

                paths.append(ImpactPath(
                    path=new_path,
                    risk=risk,
                    confidence=confidence,
                    evidence=evidence,
                    needs_human_review=needs_review,
                ))
                queue.append((importer, new_path, depth + 1))

    return paths
