from __future__ import annotations
import json
import re
import subprocess
from dataclasses import dataclass
from typing import Optional

from api_client import call_claude
from config import Config
from phases.context_pack import ContextPack
from phases.context_savings import estimate_diff_tokens, estimate_tokens, build_savings_summary


@dataclass
class BlastRadiusItem:
    file: str
    line: int
    symbol: str
    risk: str
    confidence: str
    reason: str
    suggestion: Optional[str] = None
    needs_human_review: bool = False


_SYSTEM_PROMPT = """\
你是资深代码审查工程师，专注于分析代码改动的爆炸范围（Blast Radius）。

{skill_context}

你将收到一个结构化上下文包，包含：
- changed_symbols: 改动的函数/组件符号
- impact_paths: 影响链路，每条附有风险等级、置信度和证据
- related_rules: 相关团队规则
- review_focus: 重点审查方向
- file_history: 改动文件的历史问题记录（flagged_count=历史被标记次数，recent_issues=最近问题）
- caller_contexts: 改动符号的实际调用点采样，每个符号附 ±5 行代码片段

caller_contexts 使用规则（重要）：
  - callers 为空 → 该符号在项目内无直接调用，内部改动风险低，不应盲目标 high；
  - snippet 中调用方未使用改动的属性/返回值 → 不应将其标为 high；
  - total_callers_found 远大于已展示数量 → 影响广泛，应提高警惕；
  - 没有 caller_contexts 字段时，按 impact_paths 常规判断。

基于上下文包中的证据链评估风险。高风险低置信度项标注 needs_human_review=true。

{review_questions_section}
以 JSON 数组输出，每个元素包含：
- file: 受影响文件路径（字符串）
- line: 行号（整数）
- symbol: 改动的符号名（字符串）
- risk: "high" | "medium" | "low"
- confidence: "high" | "medium" | "low"
- reason: 影响原因（中文）
- suggestion: 修复建议，无则 null
- needs_human_review: bool

file_history 字段记录了改动文件在历史审查中的问题模式：
  - flagged_count 高（≥3）说明该文件是反复出现问题的"慢性病"区域，应提高风险等级；
  - recent_issues 里的 high 风险说明该文件近期已有严重问题，本次改动更需谨慎。
  - 若 file_history 为空，说明没有历史数据，按常规判断即可。

只输出 JSON 数组，不要其他内容。"""


def extract_changed_symbols(diff: str) -> list[str]:
    symbols: set[str] = set()
    patterns = [
        r"^\+[^+].*?(?:async\s+)?function\s+(\w+)",
        r"^\+[^+].*?(?:const|let|var)\s+(\w+)\s*=\s*(?:async\s+)?\(",
        r"^\+[^+].*?(?:const|let|var)\s+(\w+)\s*=\s*(?:async\s+)?function",
        r"^\+[^+].*?export\s+(?:const|let)\s+(\w+)",
        r"^\+[^+].*?class\s+(\w+)",
    ]
    for line in diff.split("\n"):
        for pat in patterns:
            m = re.search(pat, line)
            if m:
                symbols.add(m.group(1))
    return list(symbols)


def find_usages_in_project(symbols: list[str], ignore_dirs: list[str]) -> str:
    if not symbols:
        return ""
    results: list[str] = []
    for symbol in symbols[:10]:
        cmd = [
            "grep", "-rn",
            "--include=*.vue", "--include=*.ts",
            "--include=*.js", "--include=*.tsx",
            symbol, ".",
        ]
        for d in ignore_dirs:
            cmd.extend(["--exclude-dir", d.rstrip("/")])
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8', timeout=10)
            if result.stdout.strip():
                results.append(f"# `{symbol}` 的使用位置:\n{result.stdout[:3000]}")
        except subprocess.TimeoutExpired:
            results.append(f"# `{symbol}`: 搜索超时")
    return "\n".join(results)


def analyze(
    diff: str,
    skill_context: str,
    config: Config,
    context_pack: "ContextPack | None" = None,
    project_root: str = ".",
    detail_level: str = "standard",
) -> tuple[list[BlastRadiusItem], dict]:
    if context_pack is not None:
        _pack_json = json.dumps(context_pack.to_dict(), ensure_ascii=False, indent=2)
        # Baseline = what we would send if passing the full diff
        _baseline_user = (
            f"## 结构化上下文包\n\n```json\n{_pack_json}\n```\n\n"
            f"## Git Diff（完整）\n\n```diff\n{diff}\n```"
        )
        baseline = estimate_tokens(_baseline_user)

        if detail_level == "verbose":
            user = _baseline_user
        else:
            from phases.context_builder import extract_diff_hunks_for_symbols
            filtered_diff = extract_diff_hunks_for_symbols(diff, context_pack.changed_symbols)
            user = (
                f"## 结构化上下文包\n\n```json\n{_pack_json}\n```\n\n"
                f"## 改动 Diff（仅相关函数）\n\n```diff\n{filtered_diff}\n```"
            )
    else:
        baseline = estimate_diff_tokens(diff)
        symbols = extract_changed_symbols(diff)
        usages = find_usages_in_project(symbols, config.privacy.ignore)
        user = (
            f"## Git Diff\n\n```diff\n{diff}\n```\n\n"
            f"## 调用关系\n\n{usages or '（未找到外部调用）'}"
        )

    if context_pack and context_pack.review_questions:
        rq = "\n".join(f"- {q}" for q in context_pack.review_questions)
        review_questions_section = (
            f"以下是基于代码图谱自动发现的审查关注点，请在审查时优先回应这些问题：\n{rq}\n\n"
        )
    else:
        review_questions_section = ""

    system = _SYSTEM_PROMPT.format(
        skill_context=skill_context or "",
        review_questions_section=review_questions_section,
    )
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
        BlastRadiusItem(
            file=item.get("file", ""),
            line=int(item.get("line", 0)),
            symbol=item.get("symbol", ""),
            risk=item.get("risk", "low"),
            confidence=item.get("confidence", "medium"),
            reason=item.get("reason", ""),
            suggestion=item.get("suggestion"),
            needs_human_review=bool(item.get("needs_human_review", False)),
        )
        for item in items_raw
        if isinstance(item, dict)
    ]
    return items, savings
