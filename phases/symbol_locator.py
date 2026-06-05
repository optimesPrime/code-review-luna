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
