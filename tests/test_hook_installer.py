from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest
from hook_installer import install, uninstall, HOOK_MARKER, is_managed


# ── helpers ──────────────────────────────────────────────────────────────────

def _make_git_dir(tmp_path: Path) -> Path:
    """Create a minimal fake git repo structure."""
    git_dir = tmp_path / ".git" / "hooks"
    git_dir.mkdir(parents=True)
    return tmp_path


# ── install ───────────────────────────────────────────────────────────────────

def test_install_creates_hook_file(tmp_path):
    root = _make_git_dir(tmp_path)
    result = install(git_root=str(root))
    hook = root / ".git" / "hooks" / "pre-commit"
    assert result is True
    assert hook.exists()


def test_install_hook_is_executable(tmp_path):
    root = _make_git_dir(tmp_path)
    install(git_root=str(root))
    hook = root / ".git" / "hooks" / "pre-commit"
    assert os.access(hook, os.X_OK)


def test_install_hook_contains_luna_static(tmp_path):
    root = _make_git_dir(tmp_path)
    install(git_root=str(root))
    content = (root / ".git" / "hooks" / "pre-commit").read_text()
    # luna bin may be an absolute path, so check for "static" and "--staged" separately
    assert "static" in content
    assert "--staged" in content


def test_install_hook_contains_marker(tmp_path):
    root = _make_git_dir(tmp_path)
    install(git_root=str(root))
    content = (root / ".git" / "hooks" / "pre-commit").read_text()
    assert HOOK_MARKER in content


def test_install_hook_uses_absolute_path(tmp_path):
    root = _make_git_dir(tmp_path)
    install(git_root=str(root), luna_bin="/usr/local/bin/luna")
    content = (root / ".git" / "hooks" / "pre-commit").read_text()
    assert "/usr/local/bin/luna" in content


def test_install_pre_push_hook(tmp_path):
    root = _make_git_dir(tmp_path)
    result = install(hook_type="pre-push", git_root=str(root))
    assert result is True
    assert (root / ".git" / "hooks" / "pre-push").exists()


def test_install_does_not_overwrite_existing_non_luna_hook(tmp_path):
    root = _make_git_dir(tmp_path)
    hook = root / ".git" / "hooks" / "pre-commit"
    hook.write_text("#!/bin/sh\necho 'existing hook'\n")
    result = install(git_root=str(root))
    assert result is False
    # Original content preserved
    assert "existing hook" in hook.read_text()


def test_install_overwrites_existing_luna_hook(tmp_path):
    root = _make_git_dir(tmp_path)
    hook = root / ".git" / "hooks" / "pre-commit"
    hook.write_text(f"#!/bin/sh\n{HOOK_MARKER}\nold content\n")
    result = install(git_root=str(root))
    assert result is True
    assert "old content" not in hook.read_text()


def test_install_with_config_path(tmp_path):
    root = _make_git_dir(tmp_path)
    install(git_root=str(root), config_path="/home/user/.luna/config.yaml")
    content = (root / ".git" / "hooks" / "pre-commit").read_text()
    assert "--config" in content
    assert "/home/user/.luna/config.yaml" in content


# ── uninstall ─────────────────────────────────────────────────────────────────

def test_uninstall_removes_luna_hook(tmp_path):
    root = _make_git_dir(tmp_path)
    install(git_root=str(root))
    result = uninstall(git_root=str(root))
    assert result is True
    assert not (root / ".git" / "hooks" / "pre-commit").exists()


def test_uninstall_skips_non_luna_hook(tmp_path):
    root = _make_git_dir(tmp_path)
    hook = root / ".git" / "hooks" / "pre-commit"
    hook.write_text("#!/bin/sh\necho 'not luna'\n")
    result = uninstall(git_root=str(root))
    assert result is False
    assert hook.exists()


def test_uninstall_returns_false_when_no_hook(tmp_path):
    root = _make_git_dir(tmp_path)
    result = uninstall(git_root=str(root))
    assert result is False


# ── is_managed ────────────────────────────────────────────────────────────────

def test_is_managed_true_for_luna_hook(tmp_path):
    root = _make_git_dir(tmp_path)
    install(git_root=str(root))
    assert is_managed(git_root=str(root)) is True


def test_is_managed_false_when_no_hook(tmp_path):
    root = _make_git_dir(tmp_path)
    assert is_managed(git_root=str(root)) is False


def test_is_managed_false_for_non_luna_hook(tmp_path):
    root = _make_git_dir(tmp_path)
    hook = root / ".git" / "hooks" / "pre-commit"
    hook.write_text("#!/bin/sh\necho hi\n")
    assert is_managed(git_root=str(root)) is False
