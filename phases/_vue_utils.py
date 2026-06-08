# phases/_vue_utils.py
from __future__ import annotations
import re
from pathlib import Path


def extract_vue_script(path: Path) -> tuple[bytes, int]:
    """Extract <script> or <script setup> block from a Vue SFC.

    Prefers <script setup> over <script> when both exist.
    Skips blocks with src= (external references).
    Returns (content_bytes, line_offset) where line_offset is the number of
    lines before the script content in the original file.
    Returns (b"", 0) if no usable script block found.
    """
    try:
        content = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return (b"", 0)

    best: tuple[str, int] | None = None
    for m in re.finditer(r"<script\b([^>]*)>(.*?)</script>", content, re.DOTALL):
        attrs, body = m.group(1), m.group(2)
        if re.search(r'\bsrc\s*=', attrs):
            continue
        offset = content[:m.start(2)].count("\n")
        is_setup = "setup" in attrs
        if best is None or is_setup:
            best = (body, offset)
        if is_setup:
            break

    if best is None:
        return (b"", 0)
    body_str, offset = best
    return (body_str.encode("utf-8", errors="ignore"), offset)
