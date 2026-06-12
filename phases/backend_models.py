# phases/backend_models.py
from __future__ import annotations
from dataclasses import dataclass, field


@dataclass
class BackendChangedSymbol:
    file: str
    symbol: str
    symbol_type: str
    class_name: str
    start_line: int
    change_type: str
    attributes: list[str] = field(default_factory=list)
    evidence: str = ""

    @property
    def node_id(self) -> str:
        return f"{self.file}:{self.class_name}.{self.symbol}"


@dataclass
class BackendGraphNode:
    id: str
    node_type: str
    file: str
    name: str
    line: int = 0
    attributes: list[str] = field(default_factory=list)


@dataclass
class BackendGraphEdge:
    source: str
    target: str
    edge_type: str
    evidence: str
    confidence: str = "high"


@dataclass
class BackendContextGraph:
    nodes: dict[str, BackendGraphNode] = field(default_factory=dict)
    edges: list[BackendGraphEdge] = field(default_factory=list)
    outgoing: dict[str, list[BackendGraphEdge]] = field(default_factory=dict)
    # incoming reserved for reverse traversal (impact-on-callers analysis)
    incoming: dict[str, list[BackendGraphEdge]] = field(default_factory=dict)

    def add_node(self, node: BackendGraphNode) -> None:
        self.nodes[node.id] = node

    def add_edge(self, edge: BackendGraphEdge) -> None:
        self.edges.append(edge)
        self.outgoing.setdefault(edge.source, []).append(edge)
        self.incoming.setdefault(edge.target, []).append(edge)


@dataclass
class BackendImpactPath:
    path: list[str]
    risk: str
    confidence: str
    evidence: str
    rule_hits: list[str] = field(default_factory=list)
    needs_human_review: bool = False


@dataclass
class BackendContextPack:
    changed_symbols: list[BackendChangedSymbol]
    edges: list[BackendGraphEdge]
    impact_paths: list[BackendImpactPath]
    risk_rules_hit: list[str]
    review_focus: list[str]
    related_snippets: list[str]

    def to_dict(self) -> dict:
        _risk_order = {"high": 0, "medium": 1, "low": 2}
        top_paths = sorted(
            self.impact_paths, key=lambda p: _risk_order.get(p.risk, 3)
        )[:25]
        return {
            "changed_symbols": [
                {
                    "file": s.file,
                    "symbol": s.symbol,
                    "symbol_type": s.symbol_type,
                    "class_name": s.class_name,
                    "start_line": s.start_line,
                    "change_type": s.change_type,
                    "attributes": s.attributes,
                    "evidence": s.evidence,
                }
                for s in self.changed_symbols
            ],
            "edges": [
                {
                    "source": e.source,
                    "target": e.target,
                    "edge_type": e.edge_type,
                    "evidence": e.evidence,
                    "confidence": e.confidence,
                }
                for e in self.edges[:30]
            ],
            "impact_paths": [
                {
                    "path": p.path,
                    "risk": p.risk,
                    "confidence": p.confidence,
                    "evidence": p.evidence,
                    "rule_hits": p.rule_hits,
                    "needs_human_review": p.needs_human_review,
                }
                for p in top_paths
            ],
            "risk_rules_hit": self.risk_rules_hit,
            "review_focus": self.review_focus,
            "related_snippets": self.related_snippets,
        }


@dataclass
class BackendReviewItem:
    file: str
    line: int
    symbol: str
    risk: str
    confidence: str
    category: str
    reason: str
    evidence: str
    suggestion: str | None = None
    needs_human_review: bool = False
