from __future__ import annotations
import os
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from phases.symbol_locator import ChangedSymbol


@dataclass
class CallerSnippet:
    file: str
    line: int
    snippet: str
    language: str


@dataclass
class SymbolCallers:
    symbol: str
    callers: list[CallerSnippet]
    total_count: int


_COMMENT_PREFIXES = ("#", "//", "/*", "*")

_IMPORT_PREFIXES = (
    "import ",   # Python: import x / TS/JS/Java: import X from '...'
    "from ",     # Python: from x import y
    "import{",   # TS/JS 无空格: import{X} from '...'
    "using ",    # C#: using X;
    "use ",      # PHP: use X;
    "require ",  # Ruby: require 'x'
)

_INCLUDE_EXTENSIONS = [
    "*.py", "*.ts", "*.tsx", "*.js", "*.vue",
    "*.java", "*.go", "*.cs", "*.rb", "*.php",
]

_EXT_LANGUAGE = {
    ".py": "python",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".js": "javascript",
    ".vue": "vue",
    ".java": "java",
    ".go": "go",
    ".cs": "csharp",
    ".rb": "ruby",
    ".php": "php",
}


def _common_prefix_length(a: str, b: str) -> int:
    parts_a = a.split(os.sep)
    parts_b = b.split(os.sep)
    n = 0
    for x, y in zip(parts_a, parts_b):
        if x == y:
            n += 1
        else:
            break
    return n


_CALLER_EXTENSIONS = {os.path.splitext(e)[1] for e in _INCLUDE_EXTENSIONS}


def grep_call_sites(
    symbol: str,
    project_root: str,
    ignore_dirs: list[str],
    self_file: str | None = None,
) -> list[tuple[str, int]]:
    ignore_set = {d.rstrip("/").rstrip("\\") for d in ignore_dirs}
    raw_lines: list[str] = []
    for dirpath, dirnames, filenames in os.walk(project_root):
        dirnames[:] = [d for d in dirnames if d not in ignore_set]
        for filename in filenames:
            if os.path.splitext(filename)[1] in _CALLER_EXTENSIONS:
                filepath = os.path.join(dirpath, filename)
                try:
                    with open(filepath, encoding='utf-8', errors='ignore') as f:
                        for lineno, line in enumerate(f, 1):
                            if symbol in line:
                                raw_lines.append(f"{filepath}:{lineno}:{line.rstrip()}")
                except OSError:
                    pass

    self_norm = os.path.normpath(self_file) if self_file else None
    hits: list[tuple[str, int]] = []

    for raw_line in raw_lines:
        parts = raw_line.split(":", 2)
        if len(parts) < 3:
            continue
        file_path, line_str, content = parts[0], parts[1], parts[2]
        try:
            line_no = int(line_str)
        except ValueError:
            continue

        # Exclude self file
        if self_norm and os.path.normpath(file_path) == self_norm:
            continue

        # Exclude comment lines
        stripped = content.lstrip()
        if stripped.startswith(_COMMENT_PREFIXES):
            continue

        # Exclude import lines
        if stripped.startswith(_IMPORT_PREFIXES):
            continue

        # Exclude pure type-annotation lines (no real call or attribute access)
        is_real_usage = f"{symbol}(" in content or f"{symbol}." in content
        if not is_real_usage:
            type_markers = (
                f": {symbol}",
                f"->{symbol}",
                f"-> {symbol}",
                f"[{symbol}",
                f"| {symbol}",
                f"{symbol}]",
                f"{symbol},",
            )
            if any(m in content for m in type_markers):
                continue

        hits.append((file_path, line_no))

    # Sort: closest directory to self_file first, then by (file, line)
    if self_norm:
        self_dir = os.path.dirname(self_norm)
        hits.sort(key=lambda h: (-_common_prefix_length(h[0], self_dir), h[0], h[1]))
    else:
        hits.sort(key=lambda h: (h[0], h[1]))

    return hits


def _detect_language(file_path: str) -> str:
    ext = os.path.splitext(file_path)[1].lower()
    return _EXT_LANGUAGE.get(ext, "unknown")


def _module_name_fallback_hits(
    sym_file: str,
    project_root: str,
    ignore_dirs: list[str],
) -> list[tuple[str, int]]:
    """
    当 grep symbol 名找不到直接调用时（工厂函数模式），
    改用模块名（文件名去路径和扩展名）做 grep。
    例：symbol=ContextPack, file=phases/context_pack.py
    → grep "context_pack" 能找到 context_pack.review_questions 这类属性访问行。
    """
    module_stem = os.path.splitext(os.path.basename(sym_file))[0]
    if not module_stem:
        return []
    return grep_call_sites(module_stem, project_root, ignore_dirs, self_file=sym_file)


def build_caller_contexts(
    symbols: list["ChangedSymbol"],
    project_root: str,
    ignore_dirs: list[str],
    max_callers_per_symbol: int = 5,
    max_snippet_lines: int = 12,
    total_callers_cap: int = 20,
    db=None,
) -> list[SymbolCallers]:
    from phases.symbol_locator import ChangedSymbol as _CS  # noqa: F401
    results: list[SymbolCallers] = []
    total_shown = 0

    for sym in symbols:
        self_file = sym.file if sym.file else None
        all_hits = grep_call_sites(
            sym.symbol, project_root, ignore_dirs, self_file=self_file
        )

        # Fallback：grep symbol 名无结果时，改搜模块名（覆盖工厂函数模式）
        if not all_hits and self_file:
            all_hits = _module_name_fallback_hits(self_file, project_root, ignore_dirs)

        total_count = len(all_hits)
        capped_hits = all_hits[:max_callers_per_symbol]

        callers: list[CallerSnippet] = []
        for file_path, line_no in capped_hits:
            if total_shown >= total_callers_cap:
                break
            snippet = extract_call_snippet(file_path, line_no, max_lines=max_snippet_lines)
            callers.append(CallerSnippet(
                file=file_path,
                line=line_no,
                snippet=snippet,
                language=_detect_language(file_path),
            ))
            total_shown += 1

        results.append(SymbolCallers(
            symbol=sym.symbol,
            callers=callers,
            total_count=total_count,
        ))

    return results


def extract_call_snippet(
    file_path: str,
    line: int,
    context_lines: int = 5,
    max_lines: int = 12,
) -> str:
    try:
        with open(file_path, encoding="utf-8", errors="replace") as f:
            all_lines = f.readlines()
    except OSError:
        return ""

    start = max(0, line - context_lines - 1)
    end = min(len(all_lines), line + context_lines)
    window = all_lines[start:end]

    if len(window) > max_lines:
        window = window[:max_lines]
        return "".join(window).rstrip("\n") + "\n... (truncated)"

    return "".join(window).rstrip("\n")
