from __future__ import annotations

import os
import shutil
from pathlib import Path

HOOK_MARKER = "# luna-managed"

_HOOK_TEMPLATE = """\
#!/bin/sh
{marker} — 由 luna install-hook 生成，luna uninstall-hook 可移除
# 静态规则检查（不调用 LLM，< 1 秒）：数据库迁移风险 + API 契约变更
# 跳过检查：git commit --no-verify
"{luna_bin}" static --staged{config_flag}
"""


def _hook_path(hook_type: str, git_root: str) -> Path:
    return Path(git_root) / ".git" / "hooks" / hook_type


def _detect_luna_bin() -> str:
    """Return the absolute path to the luna executable, fallback to 'luna'."""
    return shutil.which("luna") or "luna"


def install(
    hook_type: str = "pre-commit",
    config_path: str = "",
    git_root: str = ".",
    luna_bin: str = "",
) -> bool:
    """Install luna as a git hook.

    Returns True on success, False if a non-luna hook already exists.
    """
    path = _hook_path(hook_type, git_root)
    path.parent.mkdir(parents=True, exist_ok=True)

    if path.exists():
        existing = path.read_text(encoding="utf-8", errors="ignore")
        if HOOK_MARKER not in existing:
            return False  # don't overwrite someone else's hook

    bin_path = luna_bin or _detect_luna_bin()
    config_flag = f" --config {config_path}" if config_path else ""

    script = _HOOK_TEMPLATE.format(
        marker=HOOK_MARKER,
        luna_bin=bin_path,
        config_flag=config_flag,
    )
    path.write_text(script, encoding="utf-8")
    path.chmod(0o755)
    return True


def uninstall(hook_type: str = "pre-commit", git_root: str = ".") -> bool:
    """Remove a luna-managed git hook.

    Returns True if removed, False if hook doesn't exist or isn't luna-managed.
    """
    path = _hook_path(hook_type, git_root)
    if not path.exists():
        return False
    content = path.read_text(encoding="utf-8", errors="ignore")
    if HOOK_MARKER not in content:
        return False
    path.unlink()
    return True


def is_managed(hook_type: str = "pre-commit", git_root: str = ".") -> bool:
    """Return True if the hook exists and was installed by luna."""
    path = _hook_path(hook_type, git_root)
    if not path.exists():
        return False
    return HOOK_MARKER in path.read_text(encoding="utf-8", errors="ignore")
