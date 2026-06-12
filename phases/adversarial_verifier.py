from __future__ import annotations
import json
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

from api_client import call_claude

if TYPE_CHECKING:
    from phases.blast_radius import BlastRadiusItem
    from phases.context_pack import ContextPack
    from config import Config


@dataclass
class RefutedFinding:
    item: "BlastRadiusItem"
    adv_reason: str

_SYSTEM = """\
你是代码审查质疑者。你将收到一批"high 风险但置信度非 high"的审查发现，以及相关代码上下文。
你的任务是：逐条尝试证明每个发现是误报（false positive）。

判断原则：
- 调用方代码中没有使用改动的属性或返回值 → 不是真实风险（confirmed: false）
- 改动的符号在项目内没有调用方 → 不是真实风险
- 风险理由与代码上下文明显不符 → 不是真实风险
- 找不到足够的反驳理由 → 保留（confirmed: true）

以 JSON 数组输出，每个元素：
- index: 原始 finding 的序号（整数）
- confirmed: 是否确认为真实风险（bool）
- reason: 判断理由（中文，一句话）

只输出 JSON 数组，不要其他内容。"""


def filter_diff_for_files(diff: str, files: set[str]) -> str:
    """Return only diff hunks whose b/ path matches a file in `files`."""
    if not files:
        return ""
    result: list[str] = []
    lines = diff.split("\n")
    i = 0
    file_header: list[str] = []
    active = False
    while i < len(lines):
        line = lines[i]
        if line.startswith("diff --git "):
            parts = line.split(" b/", 1)
            diff_path = parts[1].strip() if len(parts) == 2 else ""
            active = any(diff_path == f or diff_path.endswith("/" + f) for f in files)
            file_header = [line]
            i += 1
            while i < len(lines) and not lines[i].startswith("@@") and not lines[i].startswith("diff --git "):
                file_header.append(lines[i])
                i += 1
            continue
        if active:
            if file_header:
                result.extend(file_header)
                file_header = []
            result.append(line)
        i += 1
    return "\n".join(result)


def build_adversarial_context(
    diff: str,
    finding_files: set[str],
    context_pack: "ContextPack",
) -> str:
    """Build adversarial context from diff + caller_contexts scoped to finding files."""
    finding_sym_names = {
        s.symbol for s in context_pack.changed_symbols
        if s.file in finding_files
    }
    filtered_diff = filter_diff_for_files(diff, finding_files)

    caller_lines: list[str] = []
    for sc in context_pack.caller_contexts:
        if sc.symbol in finding_sym_names:
            caller_lines.append(f"symbol={sc.symbol}; callers={sc.total_count}")
            for c in sc.callers[:3]:
                caller_lines.append(f"  {c.file}:{c.line}  {c.snippet}")

    callers_text = "\n".join(caller_lines[:30]) if caller_lines else "（无）"
    return (
        f"## 调用方上下文\n{callers_text}\n\n"
        f"## 相关 diff\n```diff\n{filtered_diff[:4000]}\n```"
    )


def adversarial_verify(
    items: list["BlastRadiusItem"],
    context_snippet: str,
    config: "Config | None",
) -> tuple[list["BlastRadiusItem"], list[RefutedFinding]]:
    if not items:
        return [], []

    to_verify = [(i, item) for i, item in enumerate(items) if item.risk == "high" and item.confidence != "high"]
    if not to_verify:
        return list(items), []

    findings_text = json.dumps(
        [{"index": i, "file": item.file, "symbol": item.symbol, "reason": item.reason, "confidence": item.confidence}
         for i, item in to_verify],
        ensure_ascii=False, indent=2,
    )
    user = (
        f"## 待验证 findings\n\n```json\n{findings_text}\n```\n\n"
        f"## 相关代码上下文\n\n```\n{context_snippet}\n```"
    )

    try:
        raw = call_claude(_SYSTEM, user, config)
        match = re.search(r"\[.*\]", raw, re.DOTALL)
        verdicts = json.loads(match.group()) if match else []
    except Exception:
        return list(items), []

    verdict_map = {v["index"]: v for v in verdicts if isinstance(v, dict)}
    verify_indices = {i for i, _ in to_verify}

    survivors: list["BlastRadiusItem"] = []
    refuted: list[RefutedFinding] = []
    for i, item in enumerate(items):
        if i in verify_indices and not verdict_map.get(i, {}).get("confirmed", True):
            refuted.append(RefutedFinding(item=item, adv_reason=verdict_map[i].get("reason", "")))
        else:
            survivors.append(item)
    return survivors, refuted
