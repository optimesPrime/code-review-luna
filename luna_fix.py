from __future__ import annotations

import dataclasses
import json
import re
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from config import Config
    from terminal_renderer import FixCandidate


def load_latest_report(reports_dir: str) -> list["FixCandidate"] | None:
    """Read fix_candidates from latest.json in reports_dir. Returns None if missing."""
    from terminal_renderer import FixCandidate

    path = Path(reports_dir) / "latest.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None

    candidates = []
    for d in data.get("fix_candidates", []):
        try:
            candidates.append(FixCandidate(**{
                k: d.get(k, v)
                for k, v in dataclasses.asdict(FixCandidate(0, "", "", "", "", "")).items()
            }))
        except TypeError:
            pass
    return candidates


def generate_fix(
    candidate: "FixCandidate",
    source: str,
    cfg: "Config | None",
) -> tuple[str | None, str | None]:
    """Call LLM to generate a unified diff for the given fix candidate.

    Returns (patch, raw_response):
    - patch: unified diff string if extraction succeeded, else None
    - raw_response: the full LLM response text, or None if LLM failed/skipped
    """
    if candidate.mode == "manual":
        return None, None

    if cfg is None:
        return None, None

    from api_client import call_claude as call_llm

    system_prompt = (
        "你是代码修复助手。只修复指定的一处问题，最小改动。\n"
        "优先输出标准 unified diff 格式（以 ```diff 代码块包裹）。\n"
        "如果无法生成 diff，直接给出修改后的完整代码片段，并说明在哪一行修改。\n"
        "不做其它重构，不改变无关代码。"
    )
    user_prompt = (
        f"文件：{candidate.file}\n"
        f"问题：{candidate.title}\n"
        f"证据：{candidate.evidence}\n"
        f"建议：{candidate.suggestion}\n\n"
        f"当前文件内容：\n{source}"
    )

    try:
        raw = call_llm(system_prompt, user_prompt, cfg)
    except Exception as e:
        return None, f"LLM 调用失败：{e}"

    patch = _extract_patch(raw)
    return patch, raw


def apply_patch(patch: str, project_root: str) -> bool:
    """Apply a unified diff patch to files under project_root.

    Pure Python implementation — no dependency on the system `patch` command.
    Returns True on success, False if patch cannot be applied cleanly.
    """
    root = Path(project_root)
    try:
        hunks = _parse_patch(patch)
    except ValueError:
        return False

    for file_path, file_hunks in hunks.items():
        abs_path = root / file_path
        if not abs_path.exists():
            return False
        try:
            lines = abs_path.read_text(encoding="utf-8").splitlines(keepends=True)
        except OSError:
            return False

        for hunk_start, removals, additions in file_hunks:
            # hunk_start is 1-based; convert to 0-based index
            idx = hunk_start - 1
            # Verify context + removal lines match
            expected = [l for l in removals if not l.startswith("+")]
            actual = lines[idx: idx + len(expected)]
            actual_stripped = [l.rstrip("\n") for l in actual]
            expected_stripped = [l[1:].rstrip("\n") if l.startswith(" ") else l[1:].rstrip("\n") for l in expected]
            if actual_stripped != expected_stripped:
                return False
            # Count lines to remove (context + removed)
            remove_count = sum(1 for l in removals if not l.startswith("+"))
            new_lines = []
            for l in additions:
                if l.startswith("+"):
                    new_lines.append(l[1:] if l[1:].endswith("\n") else l[1:] + "\n")
                elif l.startswith(" "):
                    new_lines.append(l[1:] if l[1:].endswith("\n") else l[1:] + "\n")
            lines[idx: idx + remove_count] = new_lines

        try:
            abs_path.write_text("".join(lines), encoding="utf-8")
        except OSError:
            return False

    return True


# ── Private helpers ───────────────────────────────────────────────────────────

def _extract_patch(text: str) -> str | None:
    """Extract unified diff from LLM response (inside ```diff block or raw)."""
    # Try fenced code block first
    m = re.search(r"```(?:diff)?\n(.*?)```", text, re.DOTALL)
    if m:
        return m.group(1)
    # Fall back: look for raw diff markers
    if "--- " in text and "+++ " in text and "@@ " in text:
        start = text.index("--- ")
        return text[start:]
    return None


def _parse_patch(patch: str) -> dict[str, list[tuple[int, list[str], list[str]]]]:
    """Parse unified diff into {file_path: [(start_line, context+removals, context+additions)]}."""
    result: dict[str, list] = {}
    current_file: str | None = None
    lines = patch.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.startswith("+++ "):
            # Extract file path: strip b/ prefix
            raw = line[4:].strip()
            current_file = raw[2:] if raw.startswith("b/") else raw
            if current_file not in result:
                result[current_file] = []
        elif line.startswith("@@ ") and current_file is not None:
            m = re.match(r"@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@", line)
            if not m:
                i += 1
                continue
            start = int(m.group(1))
            hunk_lines: list[str] = []
            i += 1
            while i < len(lines) and not lines[i].startswith("@@") and not lines[i].startswith("---") and not lines[i].startswith("+++"):
                hunk_lines.append(lines[i])
                i += 1
            # Split into removal side (context + -) and addition side (context + +)
            removals = [l for l in hunk_lines if not l.startswith("+")]
            additions = [l for l in hunk_lines if not l.startswith("-")]
            result[current_file].append((start, removals, additions))
            continue
        i += 1

    if not result:
        raise ValueError("No valid hunks found in patch")
    return result
