# phases/backend_risk_propagation.py
from __future__ import annotations
from collections import deque

from phases.backend_models import BackendChangedSymbol, BackendContextGraph, BackendImpactPath


_HIGH_RISK_SYMBOL_TYPES = {
    "controller_action",
    "model_property",
    "entity_property",
    "repository_method",
}

_HIGH_RISK_EDGES = {
    "requires_auth": "auth_path_changed",
    "writes_db": "db_write_path",
    "calls_external_api": "external_api_path",
    "exposes_endpoint": "endpoint_path",
}

_HIGH_RISK_KEYWORDS = {
    "auth", "authorize", "token", "permission", "role",
    "order", "trade", "pay", "payment", "amount",
    "submit", "save", "delete", "create", "update",
    "transaction", "dbcontext", "repository",
}


def propagate_backend_risk(
    symbols: list[BackendChangedSymbol],
    graph: BackendContextGraph,
    max_depth: int = 4,
) -> list[BackendImpactPath]:
    paths: list[BackendImpactPath] = []

    for symbol in symbols:
        origin_id = symbol.node_id
        risk, rules = _origin_risk(symbol)
        paths.append(BackendImpactPath(
            path=[origin_id],
            risk=risk,
            confidence="high",
            evidence=f"直接改动 {symbol.symbol_type}: {symbol.evidence}",
            rule_hits=rules,
            needs_human_review=False,
        ))

        queue: deque[tuple[str, list[str], int, list[str]]] = deque(
            [(origin_id, [origin_id], 1, rules)]
        )
        visited = {origin_id}

        while queue:
            current, current_path, depth, inherited_rules = queue.popleft()
            if depth > max_depth:
                continue

            for edge in graph.outgoing.get(current, []):
                if edge.target in visited:
                    continue
                visited.add(edge.target)
                new_path = current_path + [edge.target]
                edge_rules = inherited_rules + _edge_rules(edge.edge_type, edge.target)
                edge_risk, confidence = _classify_path(new_path, edge.edge_type, depth, edge_rules)

                paths.append(BackendImpactPath(
                    path=new_path,
                    risk=edge_risk,
                    confidence=min_confidence(confidence, edge.confidence),
                    evidence=edge.evidence,
                    rule_hits=sorted(set(edge_rules)),
                    needs_human_review=edge.confidence != "high" or depth >= 2,
                ))
                queue.append((edge.target, new_path, depth + 1, edge_rules))

    return paths


def _origin_risk(symbol: BackendChangedSymbol) -> tuple[str, list[str]]:
    rules: list[str] = []
    if symbol.symbol_type == "controller_action":
        rules.append("controller_action_changed")
    if symbol.symbol_type == "model_property":
        rules.append("model_contract_changed")
    if symbol.symbol_type == "entity_property":
        rules.append("entity_persistence_changed")
    if "Authorize" in symbol.attributes:
        rules.append("auth_attribute_present")

    lowered = f"{symbol.file} {symbol.symbol} {symbol.class_name}".lower()
    if any(keyword in lowered for keyword in _HIGH_RISK_KEYWORDS):
        rules.append("business_keyword_hit")

    risk = "high" if symbol.symbol_type in _HIGH_RISK_SYMBOL_TYPES or rules else "medium"
    return risk, rules or ["backend_symbol_changed"]


def _edge_rules(edge_type: str, target: str) -> list[str]:
    rules = []
    if edge_type in _HIGH_RISK_EDGES:
        rules.append(_HIGH_RISK_EDGES[edge_type])
    if any(keyword in target.lower() for keyword in _HIGH_RISK_KEYWORDS):
        rules.append("business_keyword_hit")
    return rules


def _classify_path(
    path: list[str],
    edge_type: str,
    depth: int,
    rules: list[str],
) -> tuple[str, str]:
    if edge_type in _HIGH_RISK_EDGES or "business_keyword_hit" in rules:
        return "high", "medium" if depth > 1 else "high"
    if depth <= 1:
        return "medium", "high"
    if depth == 2:
        return "medium", "medium"
    return "low", "low"


def min_confidence(a: str, b: str) -> str:
    order = {"low": 0, "medium": 1, "high": 2}
    return a if order[a] <= order[b] else b
