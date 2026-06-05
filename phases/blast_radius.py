from __future__ import annotations
import json
import re
import subprocess
from dataclasses import dataclass
from typing import Optional

from api_client import call_claude
from config import Config
from phases.context_pack import ContextPack


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

基于上下文包中的证据链评估风险。高风险低置信度项标注 needs_human_review=true。

以 JSON 数组输出，每个元素包含：
- file: 受影响文件路径（字符串）
- line: 行号（整数）
- symbol: 改动的符号名（字符串）
- risk: "high" | "medium" | "low"
- confidence: "high" | "medium" | "low"
- reason: 影响原因（中文）
- suggestion: 修复建议，无则 null
- needs_human_review: bool

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
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
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
) -> list[BlastRadiusItem]:
    if context_pack is not None:
        user = (
            f"## 结构化上下文包\n\n"
            f"```json\n{json.dumps(context_pack.to_dict(), ensure_ascii=False, indent=2)}\n```\n\n"
            f"## Git Diff（参考）\n\n```diff\n{diff}\n```"
        )
    else:
        symbols = extract_changed_symbols(diff)
        usages = find_usages_in_project(symbols, config.privacy.ignore)
        user = (
            f"## Git Diff\n\n```diff\n{diff}\n```\n\n"
            f"## 调用关系\n\n{usages or '（未找到外部调用）'}"
        )

    system = _SYSTEM_PROMPT.format(skill_context=skill_context or "")
    raw = call_claude(system, user, config)

    match = re.search(r"\[.*\]", raw, re.DOTALL)
    if not match:
        return []

    try:
        items_raw = json.loads(match.group())
    except json.JSONDecodeError:
        return []

    return [
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
