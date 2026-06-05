from __future__ import annotations
import re
from dataclasses import dataclass, field

@dataclass
class DiffHunk:
    start_line: int
    line_count: int


@dataclass
class DiffFile:
    path: str
    hunks: list[DiffHunk] = field(default_factory=list)
    is_new_file: bool = False
    is_deleted: bool = False


@dataclass
class ChangedSymbol:
    file: str
    symbol: str
    symbol_type: str  # "function" | "class" | "component" | "export"
    start_line: int
    change_type: str  # "added" | "modified"


_SYMBOL_PATTERNS: list[tuple[str, str]] = [
    (r"^\s*(?:export\s+)?(?:async\s+)?function\s+(\w+)", "function"),
    (r"^\s*(?:export\s+)?const\s+(\w+)\s*=\s*(?:async\s+)?\(", "function"),
    (r"^\s*(?:export\s+)?const\s+(\w+)\s*=\s*(?:async\s+)?function", "function"),
    (r"^\s*(?:export\s+)?class\s+(\w+)", "class"),
    (r"^\s*(?:const|let)\s+(\w+)\s*=\s*defineComponent\(", "component"),
    (r"^\s*(?:const|let)\s+(\w+)\s*=\s*defineStore\(", "component"),
]


def locate_symbols(file_path: str, changed_lines: list[int]) -> list[ChangedSymbol]:
    try:
        with open(file_path, encoding="utf-8", errors="ignore") as f:
            source_lines = f.readlines()
    except OSError:
        return []

    if not source_lines:
        return []

    symbols: list[ChangedSymbol] = []
    seen: set[str] = set()

    for changed_line in changed_lines:
        # Walk backwards from changed_line to find the nearest enclosing symbol
        search_from = min(changed_line - 1, len(source_lines) - 1)
        for i in range(search_from, -1, -1):
            line = source_lines[i]
            for pattern, sym_type in _SYMBOL_PATTERNS:
                m = re.search(pattern, line)
                if m:
                    symbol_name = m.group(1)
                    key = f"{file_path}:{symbol_name}"
                    if key not in seen:
                        seen.add(key)
                        symbols.append(ChangedSymbol(
                            file=file_path,
                            symbol=symbol_name,
                            symbol_type=sym_type,
                            start_line=i + 1,
                            change_type="modified",
                        ))
                    break
            else:
                continue
            break  # Found a symbol for this changed_line; move to next

    return symbols


def parse_diff(diff: str) -> list[DiffFile]:
    files: list[DiffFile] = []
    current: DiffFile | None = None

    for line in diff.splitlines():
        if line.startswith("diff --git "):
            m = re.search(r" b/(.+)$", line)
            if m:
                current = DiffFile(path=m.group(1))
                files.append(current)
        elif line.startswith("new file mode") and current:
            current.is_new_file = True
        elif line.startswith("deleted file mode") and current:
            current.is_deleted = True
        elif line.startswith("@@ ") and current:
            # @@ -old_start,old_count +new_start,new_count @@
            m = re.search(r"\+(\d+)(?:,(\d+))?", line)
            if m:
                start = int(m.group(1))
                count = int(m.group(2)) if m.group(2) is not None else 1
                current.hunks.append(DiffHunk(start_line=start, line_count=count))

    return files
