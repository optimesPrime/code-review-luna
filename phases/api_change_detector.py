from __future__ import annotations

import re
import subprocess
from pathlib import Path

from phases.openapi_analyzer import APIChangeItem

_DIFF_FILE_RE = re.compile(r"^diff --git a/\S+ b/(\S+)$", re.MULTILINE)

_OPENAPI_PATTERNS = [
    re.compile(r"(swagger|openapi|api[-_]docs?)\.(json|ya?ml)$", re.I),
]
_PROTO_PATTERN = re.compile(r"\.proto$", re.I)


def detect_schema_files(diff: str) -> tuple[list[str], list[str]]:
    """Return (openapi_files, proto_files) found in the diff."""
    openapi, proto = [], []
    for m in _DIFF_FILE_RE.finditer(diff):
        path = m.group(1)
        if any(pat.search(path) for pat in _OPENAPI_PATTERNS):
            openapi.append(path)
        elif _PROTO_PATTERN.search(path):
            proto.append(path)
    return openapi, proto


def _get_old_content(file_path: str, project_root: str) -> str | None:
    """Fetch the HEAD version of a file via git show."""
    try:
        result = subprocess.run(
            ["git", "show", f"HEAD:{file_path}"],
            capture_output=True, text=True, timeout=10,
            cwd=project_root,
        )
        return result.stdout if result.returncode == 0 else None
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return None


def _get_file_diff(diff: str, file_path: str) -> str:
    marker = f"diff --git a/{file_path} b/{file_path}"
    start = diff.find(marker)
    if start == -1:
        return ""
    end = diff.find("\ndiff --git ", start + 1)
    return diff[start:] if end == -1 else diff[start:end]


def analyze(diff: str, project_root: str) -> list[APIChangeItem]:
    """Detect OpenAPI and Protobuf schema changes in the diff."""
    from phases.openapi_analyzer import analyze as openapi_analyze
    from phases.proto_analyzer import analyze as proto_analyze

    openapi_files, proto_files = detect_schema_files(diff)
    items: list[APIChangeItem] = []

    for f in openapi_files:
        old_content = _get_old_content(f, project_root)
        if old_content is None:
            continue  # new file → no breaking changes
        try:
            new_path = Path(project_root) / f
            new_content = new_path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        items.extend(openapi_analyze(old_content, new_content, f))

    for f in proto_files:
        hunk = _get_file_diff(diff, f)
        if hunk:
            items.extend(proto_analyze(hunk, f))

    return items
