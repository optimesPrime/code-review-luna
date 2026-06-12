from __future__ import annotations
from dataclasses import dataclass, field


@dataclass
class RuntimeContext:
    project_name: str = ""
    project_root: str = ""
    project_type: str = "auto"          # "frontend" | "backend" | "fullstack" | "auto"
    diff_scope: str = "working tree"    # "staged" | "working tree" | "since <ref>"
    changed_files: int = 0
    changed_lines: int = 0
    backend_review_status: str = "skipped"  # "skipped" | "ran" | "error"
    elapsed_seconds: float = 0.0
    report_path: str = ""
    commit_hash: str = ""
