# phases/backend_review.py
from __future__ import annotations
import json
import re

from api_client import call_claude
from config import Config
from phases.backend_models import BackendContextPack, BackendReviewItem
from phases.context_savings import estimate_tokens, build_savings_summary


_SYSTEM_PROMPT = """\
你是资深多语言后端代码审查工程师，熟悉 C# ASP.NET Core、Java Spring、Python FastAPI/Django/Flask、Node.js Express/NestJS、Go Gin/Echo/Fiber、PHP Laravel/Symfony、C++ 服务端模块。

{skill_context}

你将收到结构化后端上下文包，而不是孤立 diff。必须基于上下文包里的证据审查：
- changed_symbols: 改动的后端 class/method/function/property/field/attribute/decorator/annotation
- edges: Controller/Service/Repository/Model/Entity/Auth/DB/External API 关系
- impact_paths: 风险传播链路
- risk_rules_hit: 本地风险规则命中
- review_focus: 本次重点审查方向
- related_snippets: 相关证据片段

审查重点：
1. Controller/handler/route 的鉴权、参数、状态码、异常路径。
2. Service 业务流程的漏判、空值、重复判断和失败分支。
3. Model/DTO/schema/struct 字段变化的兼容性风险。
4. Repository/ORM/DbContext/数据库查询、写库、事务、并发和软删除风险。
5. 外部接口、配置读取、中间件、guard/filter/interceptor/policy 的行为变化。
6. C++ 服务端模块的内存所有权、指针生命周期、锁、线程边界、序列化结构变化。

输出 JSON 数组。每个元素必须包含：
- file: 文件路径
- line: 行号
- symbol: 符号名
- risk: "high" | "medium" | "low"
- confidence: "high" | "medium" | "low"
- category: "controller" | "handler" | "service" | "model" | "schema" | "repository" | "auth" | "db" | "external" | "config" | "concurrency" | "memory" | "quality"
- reason: 中文风险说明
- evidence: 证据，必须引用上下文包中的符号、边、规则或片段
- suggestion: 中文建议，无则 null
- needs_human_review: bool

只输出 JSON 数组，不要输出 Markdown 或解释文字。
"""


def analyze_backend(
    context_pack: BackendContextPack,
    diff: str,
    skill_context: str,
    config: Config,
    detail_level: str = "standard",
) -> tuple[list[BackendReviewItem], dict]:
    baseline = estimate_tokens(diff)

    if detail_level == "verbose":
        user = (
            "## 后端结构化上下文包\n\n"
            f"```json\n{json.dumps(context_pack.to_dict(), ensure_ascii=False, indent=2)}\n```\n\n"
            "## Git Diff（完整）\n\n"
            f"```diff\n{diff}\n```"
        )
    else:
        user = (
            "## 后端结构化上下文包\n\n"
            f"```json\n{json.dumps(context_pack.to_dict(), ensure_ascii=False, indent=2)}\n```"
        )

    raw = call_claude(_SYSTEM_PROMPT.format(skill_context=skill_context or ""), user, config)
    savings = build_savings_summary(baseline, estimate_tokens(user))

    match = re.search(r"\[.*\]", raw, re.DOTALL)
    if not match:
        return [], savings
    try:
        parsed = json.loads(match.group())
    except json.JSONDecodeError:
        return [], savings

    items: list[BackendReviewItem] = []
    for item in parsed:
        if not isinstance(item, dict):
            continue
        items.append(BackendReviewItem(
            file=item.get("file", ""),
            line=int(item.get("line", 0)),
            symbol=item.get("symbol", ""),
            risk=item.get("risk", "low"),
            confidence=item.get("confidence", "medium"),
            category=item.get("category", "quality"),
            reason=item.get("reason", ""),
            evidence=item.get("evidence", ""),
            suggestion=item.get("suggestion"),
            needs_human_review=bool(item.get("needs_human_review", False)),
        ))
    return items, savings
