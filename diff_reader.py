from __future__ import annotations
import re
import subprocess
from typing import Optional


class DiffError(Exception):
    pass


def get_diff(staged: bool = False, since: Optional[str] = None) -> str:
    check = subprocess.run(
        ["git", "rev-parse", "--git-dir"],
        capture_output=True, text=True, encoding='utf-8',
    )
    if check.returncode != 0:
        raise DiffError("当前目录不是 git 仓库，请在项目根目录下运行 luna")

    if staged:
        cmd = ["git", "diff", "--cached"]
    elif since:
        cmd = ["git", "diff", since]
    else:
        cmd = ["git", "diff", "HEAD"]

    result = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8')
    return result.stdout


def redact(text: str, patterns: list[str]) -> str:
    for pattern in patterns:
        text = re.sub(pattern, "[REDACTED]", text)
    return text
