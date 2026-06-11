from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from phases.blast_radius import BlastRadiusItem
    from phases.risk_propagation import ImpactPath
    from phases.symbol_locator import ChangedSymbol

_BRACE_EXTS = {".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".vue", ".go", ".java", ".cs"}
_INDENT_EXTS = {".py", ".pyi"}

_HUNK_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")


def _find_symbol_end(lines: list[str], start_line: int, ext: str) -> int:
    """Return the 1-indexed end line of the symbol starting at start_line."""
    idx = start_line - 1  # convert to 0-indexed
    total = len(lines)

    if ext in _BRACE_EXTS:
        depth = 0
        found_open = False
        for i in range(idx, min(idx + 300, total)):
            for ch in lines[i]:
                if ch == "{":
                    depth += 1
                    found_open = True
                elif ch == "}":
                    depth -= 1
            if found_open and depth == 0:
                return i + 1  # 1-indexed
        return min(idx + 80, total - 1) + 1

    if ext in _INDENT_EXTS:
        def _indent(line: str) -> int:
            return len(line) - len(line.lstrip())

        if idx >= total:
            return idx + 1
        base_indent = _indent(lines[idx])
        for i in range(idx + 1, min(idx + 200, total)):
            line = lines[i]
            if line.strip() and _indent(line) <= base_indent:
                return i  # exclusive end (1-indexed: i means line i+1 is next)
        return min(idx + 80, total - 1) + 1

    # fallback
    return min(idx + 80, total - 1) + 1


def extract_relevant_snippets(
    changed_symbols: list["ChangedSymbol"],
    project_root: str,
    context_lines: int = 3,
    max_lines: int = 150,
) -> dict[str, str]:
    """Extract only the function bodies of changed symbols.

    Returns {relative_path: snippet_text}.
    Missing files are silently skipped.
    """
    from collections import defaultdict

    by_file: dict[str, list["ChangedSymbol"]] = defaultdict(list)
    for sym in changed_symbols:
        by_file[sym.file].append(sym)

    result: dict[str, str] = {}
    root = Path(project_root)

    for file_path, syms in by_file.items():
        path = Path(file_path)
        if not path.exists():
            continue
        try:
            content = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue

        lines = content.splitlines(keepends=True)
        ext = path.suffix.lower()

        # Build intervals [start, end] (1-indexed, inclusive)
        intervals: list[tuple[int, int]] = []
        for sym in syms:
            s = max(1, sym.start_line - context_lines)
            e_sym = _find_symbol_end(lines, sym.start_line, ext)
            e = min(len(lines), e_sym + context_lines)
            intervals.append((s, e))

        # Sort and merge intervals that are within 5 lines of each other
        intervals.sort()
        merged: list[tuple[int, int]] = []
        for s, e in intervals:
            if merged and s - merged[-1][1] <= 5:
                merged[-1] = (merged[-1][0], max(merged[-1][1], e))
            else:
                merged.append((s, e))

        # Extract text for each merged interval
        parts: list[str] = []
        total_so_far = 0
        for s, e in merged:
            seg_lines = lines[s - 1 : e]
            if total_so_far + len(seg_lines) > max_lines:
                remaining = max_lines - total_so_far
                seg_lines = seg_lines[:remaining]
                parts.append("".join(seg_lines) + "\n... (truncated)")
                total_so_far = max_lines
                break
            parts.append("".join(seg_lines))
            total_so_far += len(seg_lines)

        snippet = "\n".join(parts)

        # Use relative path when possible
        try:
            rel = str(path.relative_to(root))
        except ValueError:
            rel = file_path

        result[rel] = snippet

    return result


def build_minimal_context(
    symbols: list["ChangedSymbol"],
    risk_items: list["BlastRadiusItem"],
    impact_paths: list["ImpactPath"],
) -> dict:
    """Compact context: symbol names + risk summary + impact files, no source code."""
    return {
        "changed_symbols": [
            {
                "file": s.file,
                "symbol": s.symbol,
                "type": s.symbol_type,
                "line": s.start_line,
            }
            for s in symbols
        ],
        "risk_summary": {
            "high": sum(1 for i in risk_items if i.risk == "high"),
            "medium": sum(1 for i in risk_items if i.risk == "medium"),
            "top_risks": [
                {"symbol": i.symbol, "reason": i.reason[:80]}
                for i in risk_items
                if i.risk == "high"
            ][:3],
        },
        "impact_files": list(
            dict.fromkeys(p.path[-1] for p in impact_paths if p.path)
        )[:10],
        "test_gaps": [
            i.symbol for i in risk_items if i.needs_human_review
        ][:5],
    }


def extract_diff_hunks_for_symbols(
    diff: str,
    changed_symbols: list["ChangedSymbol"],
    sym_body_estimate: int = 150,
) -> str:
    """Filter a full diff to only the hunks that overlap with changed symbol ranges.

    Each symbol's range is estimated as [start_line, start_line + sym_body_estimate].
    Falls back to the original diff when changed_symbols is empty.
    """
    if not changed_symbols:
        return diff or ""

    # Build: normalized_path -> list of (start, estimated_end)
    sym_ranges: dict[str, list[tuple[int, int]]] = {}
    for sym in changed_symbols:
        # Normalize: strip leading ./ and absolute prefix so we can suffix-match
        p = sym.file
        if p.startswith("./"):
            p = p[2:]
        sym_ranges.setdefault(p, []).append((sym.start_line, sym.start_line + sym_body_estimate))

    def _matches(diff_path: str) -> list[tuple[int, int]] | None:
        for sp, ranges in sym_ranges.items():
            if diff_path == sp or diff_path.endswith("/" + sp) or sp.endswith("/" + diff_path):
                return ranges
        return None

    result: list[str] = []
    lines = diff.split("\n")
    n = len(lines)
    i = 0
    file_header: list[str] = []
    file_header_emitted = False
    active_ranges: list[tuple[int, int]] | None = None

    while i < n:
        line = lines[i]

        if line.startswith("diff --git "):
            # Extract b/ path
            parts = line.split(" b/", 1)
            diff_path = parts[1].strip() if len(parts) == 2 else ""
            active_ranges = _matches(diff_path)
            file_header = [line]
            file_header_emitted = False
            i += 1
            # Collect file header (index / --- / +++ lines)
            while i < n and not lines[i].startswith("@@") and not lines[i].startswith("diff --git "):
                file_header.append(lines[i])
                i += 1
            continue

        if line.startswith("@@"):
            hunk: list[str] = [line]
            i += 1
            while i < n and not lines[i].startswith("@@") and not lines[i].startswith("diff --git "):
                hunk.append(lines[i])
                i += 1

            if active_ranges:
                m = _HUNK_RE.match(line)
                if m:
                    h_start = int(m.group(1))
                    h_count = int(m.group(2)) if m.group(2) else 1
                    h_end = h_start + h_count - 1
                    overlaps = any(
                        h_start <= sym_end and h_end >= sym_start - 3
                        for sym_start, sym_end in active_ranges
                    )
                    if overlaps:
                        if not file_header_emitted:
                            result.extend(file_header)
                            file_header_emitted = True
                        result.extend(hunk)
            continue

        i += 1

    filtered = "\n".join(result)
    # If nothing matched (e.g. path mismatch), fall back to full diff
    return filtered if filtered.strip() else diff


def build_standard_context(
    symbols: list["ChangedSymbol"],
    risk_items: list["BlastRadiusItem"],
    impact_paths: list["ImpactPath"],
    diff: str,
    project_root: str,
) -> dict:
    """Standard context: minimal + relevant code snippets."""
    ctx = build_minimal_context(symbols, risk_items, impact_paths)
    ctx["relevant_snippets"] = extract_relevant_snippets(symbols, project_root)
    return ctx


def build_verbose_context(
    symbols: list["ChangedSymbol"],
    risk_items: list["BlastRadiusItem"],
    impact_paths: list["ImpactPath"],
    diff: str,
    project_root: str,
) -> dict:
    """Verbose context: standard + full diff + full impact chains."""
    ctx = build_standard_context(symbols, risk_items, impact_paths, diff, project_root)
    ctx["full_diff"] = diff
    ctx["full_impact_chains"] = [
        {"path": p.path, "risk": p.risk, "evidence": p.evidence}
        for p in impact_paths
    ]
    return ctx
