# phases/context_pack.py
from __future__ import annotations
from dataclasses import dataclass, field
from phases.symbol_locator import ChangedSymbol
from phases.risk_propagation import ImpactPath


@dataclass
class ContextPack:
    changed_symbols: list[ChangedSymbol]
    impact_paths: list[ImpactPath]
    related_rules: list[str]
    related_tests: list[str]
    review_focus: list[str] = field(default_factory=list)
    review_questions: list[str] = field(default_factory=list)
    file_history: dict = field(default_factory=dict)  # {file: {flagged_count, recent_issues}}
    caller_contexts: list = field(default_factory=list)  # list[SymbolCallers]

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
                    "start_line": s.start_line,
                    "change_type": s.change_type,
                }
                for s in self.changed_symbols
            ],
            "impact_paths": [
                {
                    "path": p.path,
                    "risk": p.risk,
                    "confidence": p.confidence,
                    "evidence": p.evidence,
                    "needs_human_review": p.needs_human_review,
                }
                for p in top_paths
            ],
            "related_rules": self.related_rules,
            "related_tests": self.related_tests[:20],
            "review_focus": self.review_focus,
            "file_history": self.file_history,
            "caller_contexts": [
                {
                    "symbol": sc.symbol,
                    "total_callers_found": sc.total_count,
                    "callers": [
                        {
                            "file": c.file,
                            "line": c.line,
                            "snippet": c.snippet,
                            "language": c.language,
                        }
                        for c in sc.callers
                    ],
                }
                for sc in self.caller_contexts
                if sc.callers
            ],
        }


def build_context_pack(
    symbols: list[ChangedSymbol],
    impact_paths: list[ImpactPath],
    related_rules: list[str],
    related_tests: list[str],
) -> ContextPack:
    review_focus: list[str] = []

    high_risk = [p for p in impact_paths if p.risk == "high"]
    if high_risk:
        involved = sorted({p.path[-1] for p in high_risk})
        review_focus.append(
            f"发现 {len(high_risk)} 条高风险影响链路，涉及：{', '.join(involved)}"
        )

    needs_review = [p for p in impact_paths if p.needs_human_review]
    if needs_review:
        review_focus.append(f"{len(needs_review)} 条链路置信度低，需人工确认")

    if symbols:
        names = ", ".join(s.symbol for s in symbols)
        review_focus.append(f"改动符号：{names}")

    return ContextPack(
        changed_symbols=symbols,
        impact_paths=impact_paths,
        related_rules=related_rules,
        related_tests=related_tests,
        review_focus=review_focus,
    )
