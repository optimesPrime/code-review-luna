from __future__ import annotations
import json
import re
from dataclasses import dataclass
from typing import Optional

from api_client import call_claude
from config import Config
from phases.context_savings import estimate_diff_tokens, estimate_tokens, build_savings_summary


@dataclass
class CodeQualityItem:
    file: str
    line: int
    issue_type: str
    description: str
    evidence: str
    risk: str
    confidence: str
    suggestion: Optional[str] = None


_SYSTEM_PROMPT = """\
你是代码质量审查工程师，分析改动代码本身的质量问题。

检查项：
1. 冗余逻辑（重复代码、死代码）
2. 多余判断（永真/永假条件）
3. 关键路径异常处理是否完整
4. 整体流程是否能走通

{skill_context}

以 JSON 数组输出，每个元素包含：
- file: 文件路径（字符串）
- line: 行号（整数）
- issue_type: "redundant" | "dead_code" | "missing_error_handling" | "logic_gap"
- description: 问题描述（中文）
- evidence: 判断依据（引用具体代码）
- risk: "high" | "medium" | "low"
- confidence: "high" | "medium" | "low"
- suggestion: 修复建议，无则 null

只输出 JSON 数组，不要其他内容。"""


def analyze(
    diff: str,
    skill_context: str,
    config: Config,
    symbols=None,
    project_root: str = ".",
    detail_level: str = "standard",
) -> tuple[list[CodeQualityItem], dict]:
    baseline = estimate_diff_tokens(diff)
    system = _SYSTEM_PROMPT.format(skill_context=skill_context or "")

    if symbols and detail_level != "verbose":
        from phases.context_builder import extract_diff_hunks_for_symbols
        filtered_diff = extract_diff_hunks_for_symbols(diff, symbols)
        user = f"## 改动 Diff（仅相关函数）\n\n```diff\n{filtered_diff}\n```"
    else:
        user = f"## Git Diff\n\n```diff\n{diff}\n```"

    raw = call_claude(system, user, config)
    savings = build_savings_summary(baseline, estimate_tokens(user))

    match = re.search(r"\[.*\]", raw, re.DOTALL)
    if not match:
        return [], savings

    try:
        items_raw = json.loads(match.group())
    except json.JSONDecodeError:
        return [], savings

    items = [
        CodeQualityItem(
            file=item.get("file", ""),
            line=int(item.get("line", 0)),
            issue_type=item.get("issue_type", "logic_gap"),
            description=item.get("description", ""),
            evidence=item.get("evidence", ""),
            risk=item.get("risk", "low"),
            confidence=item.get("confidence", "medium"),
            suggestion=item.get("suggestion"),
        )
        for item in items_raw
        if isinstance(item, dict)
    ]
    return items, savings
