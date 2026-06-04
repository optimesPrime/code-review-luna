from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
from config import SkillEntry


@dataclass
class SkillLoadError:
    name: str
    reason: str


def load_skills(entries: list[SkillEntry]) -> tuple[str, list[SkillLoadError]]:
    parts: list[str] = []
    errors: list[SkillLoadError] = []

    for entry in entries:
        p = Path(entry.path)
        if not p.exists():
            errors.append(SkillLoadError(entry.name, f"文件不存在: {entry.path}"))
            continue
        content = p.read_text(encoding="utf-8").strip()
        if not content:
            errors.append(SkillLoadError(entry.name, f"文件为空: {entry.path}"))
            continue
        parts.append(f"# Skill: {entry.name}\n{content}")

    return "\n\n".join(parts), errors
