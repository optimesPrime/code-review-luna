# phases/backend_context_pack.py
from __future__ import annotations

from phases.backend_models import (
    BackendChangedSymbol,
    BackendContextPack,
    BackendGraphEdge,
    BackendImpactPath,
)


def build_backend_context_pack(
    symbols: list[BackendChangedSymbol],
    edges: list[BackendGraphEdge],
    impact_paths: list[BackendImpactPath],
    snippet_limit: int = 12,
) -> BackendContextPack:
    rules = sorted({rule for path in impact_paths for rule in path.rule_hits})
    uncertain = [edge for edge in edges if edge.confidence != "high"]
    review_focus = _build_review_focus(symbols, impact_paths, rules)
    snippets = [s.evidence for s in symbols if s.evidence][:snippet_limit]
    snippets.extend(edge.evidence for edge in edges[: max(0, snippet_limit - len(snippets))])

    return BackendContextPack(
        changed_symbols=symbols,
        edges=edges,
        impact_paths=impact_paths,
        risk_rules_hit=rules,
        uncertain_edges=uncertain,
        review_focus=review_focus,
        related_snippets=snippets,
    )


def _build_review_focus(
    symbols: list[BackendChangedSymbol],
    impact_paths: list[BackendImpactPath],
    rules: list[str],
) -> list[str]:
    focus: list[str] = []
    symbol_types = {s.symbol_type for s in symbols}
    if "controller_action" in symbol_types:
        focus.append("检查 Controller 接口入口、状态码、鉴权和异常路径")
    if "model_property" in symbol_types or "model_contract_changed" in rules:
        focus.append("检查 Request/Response Model 字段变化是否破坏调用方兼容性")
    if "db_write_path" in rules:
        focus.append("检查 Repository/DbContext 写库、事务和并发更新风险")
    if "auth_path_changed" in rules or any("Authorize" in s.attributes for s in symbols):
        focus.append("检查 Authorize/权限相关逻辑是否被遗漏或放宽")
    if "external_api_path" in rules:
        focus.append("检查外部接口调用参数、失败重试和异常处理")

    high_count = sum(1 for p in impact_paths if p.risk == "high")
    if high_count:
        focus.append(f"优先审查 {high_count} 条 high 风险后端影响链路")

    return focus or ["检查后端改动的业务流程、异常路径和数据一致性"]
