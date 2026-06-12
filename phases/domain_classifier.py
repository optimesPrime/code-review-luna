from __future__ import annotations
import fnmatch
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from phases.symbol_locator import ChangedSymbol
    from phases.blast_radius import BlastRadiusItem
    from config import DomainEntry

_FALLBACK = "_unclassified"


def classify_symbols_by_domain(
    symbols: list["ChangedSymbol"],
    domain_configs: list["DomainEntry"],
) -> dict[str, list["ChangedSymbol"]]:
    if not symbols:
        return {}
    result: dict[str, list["ChangedSymbol"]] = {}
    for sym in symbols:
        matched = next(
            (d.name for d in domain_configs if any(fnmatch.fnmatch(sym.file, p) for p in d.patterns)),
            None,
        )
        result.setdefault(matched or _FALLBACK, []).append(sym)
    return result


def filter_diff_for_files(diff: str, files: set[str]) -> str:
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
            active = any(
                diff_path == f or diff_path.endswith("/" + f)
                for f in files
            )
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


def group_findings_by_domain(
    items: list["BlastRadiusItem"],
    domain_map: dict[str, list["ChangedSymbol"]],
) -> dict[str, list["BlastRadiusItem"]]:
    if not items:
        return {}
    file_to_domain = {
        sym.file: domain_name
        for domain_name, syms in domain_map.items()
        for sym in syms
    }
    result: dict[str, list["BlastRadiusItem"]] = {}
    for item in items:
        domain = file_to_domain.get(item.file, _FALLBACK)
        result.setdefault(domain, []).append(item)
    return result
