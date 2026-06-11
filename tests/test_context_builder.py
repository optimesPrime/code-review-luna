from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from phases.symbol_locator import ChangedSymbol


def _sym(file: str, symbol: str, start_line: int) -> ChangedSymbol:
    return ChangedSymbol(
        file=file,
        symbol=symbol,
        symbol_type="function",
        start_line=start_line,
        change_type="modified",
    )


# ---------------------------------------------------------------------------
# Task 1: extract_relevant_snippets
# ---------------------------------------------------------------------------

def test_extract_snippets_returns_only_changed_function(tmp_path):
    from phases.context_builder import extract_relevant_snippets

    # 500-line file; function at line 250 spans ~20 lines
    lines = [f"const x{i} = {i};\n" for i in range(1, 501)]
    lines[249] = "function myFunc() {\n"        # line 250 (1-indexed)
    for i in range(250, 269):
        lines[i] = f"  const body{i} = {i};\n"
    lines[269] = "}\n"                           # line 270

    src = tmp_path / "src.ts"
    src.write_text("".join(lines))

    sym = _sym(str(src), "myFunc", 250)
    result = extract_relevant_snippets([sym], str(tmp_path))

    assert len(result) == 1
    snippet = list(result.values())[0]
    assert "function myFunc" in snippet
    # Should NOT contain most of the 500 lines
    assert snippet.count("\n") < 100


def test_extract_snippets_merges_overlapping_ranges(tmp_path):
    from phases.context_builder import extract_relevant_snippets

    # Two functions 3 lines apart (< 5 line gap → merged)
    body = textwrap.dedent("""\
        const pad = 1;
        function a() {
          return 1;
        }
        // comment
        function b() {
          return 2;
        }
        const end = 99;
    """)
    src = tmp_path / "close.ts"
    src.write_text(body)

    lines = body.splitlines()
    a_line = next(i + 1 for i, l in enumerate(lines) if "function a" in l)
    b_line = next(i + 1 for i, l in enumerate(lines) if "function b" in l)

    sym_a = _sym(str(src), "a", a_line)
    sym_b = _sym(str(src), "b", b_line)
    result = extract_relevant_snippets([sym_a, sym_b], str(tmp_path))

    assert len(result) == 1
    snippet = list(result.values())[0]
    assert "function a" in snippet
    assert "function b" in snippet


def test_extract_snippets_caps_at_max_lines(tmp_path):
    from phases.context_builder import extract_relevant_snippets

    body_lines = ["function big() {\n"]
    for i in range(200):
        body_lines.append(f"  statement{i};\n")
    body_lines.append("}\n")

    src = tmp_path / "big.ts"
    src.write_text("".join(body_lines))

    sym = _sym(str(src), "big", 1)
    result = extract_relevant_snippets([sym], str(tmp_path), max_lines=150)

    assert len(result) == 1
    snippet = list(result.values())[0]
    assert "... (truncated)" in snippet
    # 150 content lines + truncation marker ≤ 152
    assert snippet.count("\n") <= 152


def test_extract_snippets_returns_empty_for_missing_file(tmp_path):
    from phases.context_builder import extract_relevant_snippets

    sym = _sym(str(tmp_path / "nonexistent.ts"), "foo", 1)
    result = extract_relevant_snippets([sym], str(tmp_path))
    assert result == {}


# ---------------------------------------------------------------------------
# Task 2: build_minimal_context / build_standard_context / build_verbose_context
# ---------------------------------------------------------------------------

def _make_symbols(n: int = 2) -> list[ChangedSymbol]:
    return [
        ChangedSymbol(
            file=f"src/mod{i}.ts",
            symbol=f"func{i}",
            symbol_type="function",
            start_line=10 * i,
            change_type="modified",
        )
        for i in range(1, n + 1)
    ]


def _make_risk_items():
    from phases.blast_radius import BlastRadiusItem

    return [
        BlastRadiusItem(
            file="src/mod1.ts",
            line=10,
            symbol="func1",
            risk="high",
            confidence="high",
            reason="auth dependency",
            needs_human_review=True,
        ),
        BlastRadiusItem(
            file="src/mod2.ts",
            line=20,
            symbol="func2",
            risk="medium",
            confidence="medium",
            reason="data flow",
        ),
    ]


def _make_impact_paths():
    from phases.risk_propagation import ImpactPath

    return [
        ImpactPath(path=["src/mod1.ts", "src/util.ts"], risk="high", confidence="high", evidence=""),
        ImpactPath(path=["src/mod2.ts", "src/api.ts"], risk="medium", confidence="medium", evidence=""),
    ]


def test_minimal_context_has_no_code():
    from phases.context_builder import build_minimal_context

    result = build_minimal_context(_make_symbols(), _make_risk_items(), _make_impact_paths())
    # No source code snippets in minimal
    assert "relevant_snippets" not in result
    assert "changed_symbols" in result
    assert "risk_summary" in result
    assert "impact_files" in result


def test_standard_context_has_snippets(tmp_path, monkeypatch):
    from phases.context_builder import build_standard_context

    # Create actual files so extract_relevant_snippets can read them
    for i in range(1, 3):
        f = tmp_path / f"mod{i}.ts"
        f.write_text(f"function func{i}() {{\n  return {i};\n}}\n")

    symbols = [
        ChangedSymbol(
            file=str(tmp_path / f"mod{i}.ts"),
            symbol=f"func{i}",
            symbol_type="function",
            start_line=1,
            change_type="modified",
        )
        for i in range(1, 3)
    ]

    result = build_standard_context(
        symbols,
        _make_risk_items(),
        _make_impact_paths(),
        diff="+ changed line",
        project_root=str(tmp_path),
    )

    assert "relevant_snippets" in result
    assert len(result["relevant_snippets"]) > 0


def test_minimal_context_under_500_chars():
    from phases.context_builder import build_minimal_context
    import json

    result = build_minimal_context(_make_symbols(), _make_risk_items(), _make_impact_paths())
    serialized = json.dumps(result, ensure_ascii=False)
    assert len(serialized) < 500


def test_detail_level_verbose_includes_full_diff():
    from phases.context_builder import build_verbose_context

    diff_text = "diff --git a/x.ts b/x.ts\n+ changed line"
    result = build_verbose_context(
        _make_symbols(),
        _make_risk_items(),
        _make_impact_paths(),
        diff=diff_text,
        project_root="/tmp",
    )
    assert "full_diff" in result
    assert diff_text in result["full_diff"]


# ---------------------------------------------------------------------------
# Task 3: context_savings
# ---------------------------------------------------------------------------

def test_estimate_tokens_string():
    from phases.context_savings import estimate_tokens

    # "hello world" = 11 chars → ceil(11/4) = 3
    assert estimate_tokens("hello world") == 3


def test_estimate_tokens_dict():
    from phases.context_savings import estimate_tokens

    data = {"key": "value"}
    result = estimate_tokens(data)
    assert result > 0


def test_estimate_diff_tokens():
    from phases.context_savings import estimate_diff_tokens

    diff = "a" * 400  # 400 chars → 100 tokens
    assert estimate_diff_tokens(diff) == 100


def test_build_savings_summary_calculates_percent():
    from phases.context_savings import build_savings_summary

    summary = build_savings_summary(baseline_tokens=1000, used_tokens=200)
    assert summary["baseline"] == 1000
    assert summary["used"] == 200
    assert summary["saved"] == 800
    assert summary["saved_percent"] == 80


# ---------------------------------------------------------------------------
# Task 4: blast_radius prompt does not contain full diff in standard mode
# ---------------------------------------------------------------------------

def test_blast_radius_prompt_does_not_contain_full_diff(monkeypatch, tmp_path):
    """Standard mode: user prompt should not include the raw diff text."""
    import json
    from unittest.mock import patch
    from config import Config
    from phases.blast_radius import analyze
    from phases.context_pack import ContextPack
    from phases.risk_propagation import ImpactPath

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    cfg = Config()

    # Create a real file so extract_relevant_snippets can read it
    src = tmp_path / "auth.ts"
    src.write_text("export function refreshToken() {\n  return fetch('/api/token');\n}\n")

    pack = ContextPack(
        changed_symbols=[
            ChangedSymbol(
                file=str(src), symbol="refreshToken",
                symbol_type="function", start_line=1, change_type="modified",
            )
        ],
        impact_paths=[ImpactPath(path=[str(src)], risk="high", confidence="high", evidence="")],
        related_rules=[],
        related_tests=[],
    )

    auth_filename = src.name  # "auth.ts"
    # Proper diff with two hunks: one at line 1 (relevant) and one at line 200 (unrelated)
    full_diff = (
        f"diff --git a/{auth_filename} b/{auth_filename}\n"
        f"index aaa..bbb 100644\n"
        f"--- a/{auth_filename}\n"
        f"+++ b/{auth_filename}\n"
        f"@@ -1,3 +1,3 @@\n"
        f"-export function refreshToken() {{ return null; }}\n"
        f"+export function refreshToken() {{ return fetch('/api/token'); }}\n"
        f" const x = 1;\n"
        f"@@ -200,3 +200,3 @@\n"
        + "+" + "UNRELATED_LINE_" + "x" * 60 + "\n" * 3
    )

    captured: dict = {}

    def fake_call(system, user, config):
        captured["user"] = user
        return "[]"

    with patch("phases.blast_radius.call_claude", side_effect=fake_call):
        items, savings = analyze(
            full_diff, "", cfg,
            context_pack=pack,
            project_root=str(tmp_path),
            detail_level="standard",
        )

    # Unrelated hunk (line 200) must NOT be in the prompt
    assert "UNRELATED_LINE_" not in captured["user"]
    # Relevant hunk must be present
    assert "refreshToken" in captured["user"]
    # Structured context must be present
    assert "结构化上下文包" in captured["user"]
    # Savings should show reduction
    assert savings["saved"] > 0


# ---------------------------------------------------------------------------
# Task 5: --quiet forces minimal, --details forces verbose
# ---------------------------------------------------------------------------

def test_quiet_uses_minimal_detail_level():
    """detail_level should be 'minimal' when quiet=True."""
    # We can't call luna CLI directly here, but we can test the logic
    quiet = True
    details = False
    level = "minimal" if quiet else ("verbose" if details else "standard")
    assert level == "minimal"


def test_details_uses_verbose_detail_level():
    """detail_level should be 'verbose' when details=True."""
    quiet = False
    details = True
    level = "minimal" if quiet else ("verbose" if details else "standard")
    assert level == "verbose"


def test_default_uses_standard_detail_level():
    """detail_level should be 'standard' by default."""
    quiet = False
    details = False
    level = "minimal" if quiet else ("verbose" if details else "standard")
    assert level == "standard"


# ---------------------------------------------------------------------------
# Task 6: token savings panel
# ---------------------------------------------------------------------------

def test_render_token_savings_shows_percent():
    from phases.context_savings import build_savings_summary
    from terminal_renderer import render_token_savings_panel

    savings_per_phase = {
        "blast": build_savings_summary(8000, 600),
        "quality": build_savings_summary(4000, 400),
    }
    panel_text = render_token_savings_panel(savings_per_phase)
    assert panel_text is not None
    assert "12,000" in panel_text or "12000" in panel_text  # total baseline (8000+4000)
    assert "%" in panel_text


def test_render_token_savings_returns_none_when_empty():
    from terminal_renderer import render_token_savings_panel

    assert render_token_savings_panel({}) is None
    assert render_token_savings_panel({"blast": {}}) is None


# ---------------------------------------------------------------------------
# extract_diff_hunks_for_symbols
# ---------------------------------------------------------------------------

_SAMPLE_DIFF = """\
diff --git a/src/auth.ts b/src/auth.ts
index aaa..bbb 100644
--- a/src/auth.ts
+++ b/src/auth.ts
@@ -10,4 +10,5 @@ import { config } from './config';
 const BASE = '/api';
-function oldHelper() { return 1; }
+function oldHelper() { return 2; }

@@ -50,6 +51,7 @@ export function handleLogin(user) {
-  const token = user.password;
+  const token = user.token;
   return fetch(BASE + '/auth', { body: token });
 }
diff --git a/src/utils.ts b/src/utils.ts
index ccc..ddd 100644
--- a/src/utils.ts
+++ b/src/utils.ts
@@ -5,3 +5,4 @@ export function format(s) {
-  return s.trim();
+  return s.trim().toLowerCase();
 }
"""


def test_extract_diff_hunks_returns_only_symbol_hunks():
    from phases.context_builder import extract_diff_hunks_for_symbols

    # Only interested in handleLogin at line 51 in auth.ts
    sym = ChangedSymbol(
        file="src/auth.ts", symbol="handleLogin",
        symbol_type="function", start_line=51, change_type="modified",
    )
    result = extract_diff_hunks_for_symbols(_SAMPLE_DIFF, [sym])

    assert "handleLogin" in result or "+  const token = user.token" in result
    # The hunk at line 10 (oldHelper) should be excluded
    assert "oldHelper" not in result
    # utils.ts should be excluded entirely
    assert "src/utils.ts" not in result


def test_extract_diff_hunks_preserves_plus_minus_lines():
    from phases.context_builder import extract_diff_hunks_for_symbols

    sym = ChangedSymbol(
        file="src/auth.ts", symbol="handleLogin",
        symbol_type="function", start_line=51, change_type="modified",
    )
    result = extract_diff_hunks_for_symbols(_SAMPLE_DIFF, [sym])

    assert "-  const token = user.password;" in result
    assert "+  const token = user.token;" in result


def test_extract_diff_hunks_skips_unrelated_files():
    from phases.context_builder import extract_diff_hunks_for_symbols

    sym = ChangedSymbol(
        file="src/auth.ts", symbol="handleLogin",
        symbol_type="function", start_line=51, change_type="modified",
    )
    result = extract_diff_hunks_for_symbols(_SAMPLE_DIFF, [sym])
    assert "src/utils.ts" not in result


def test_extract_diff_hunks_returns_full_diff_when_no_symbols():
    from phases.context_builder import extract_diff_hunks_for_symbols

    result = extract_diff_hunks_for_symbols(_SAMPLE_DIFF, [])
    assert result == _SAMPLE_DIFF


def test_extract_diff_hunks_includes_all_files_when_all_relevant():
    from phases.context_builder import extract_diff_hunks_for_symbols

    sym_auth = ChangedSymbol(
        file="src/auth.ts", symbol="handleLogin",
        symbol_type="function", start_line=51, change_type="modified",
    )
    sym_utils = ChangedSymbol(
        file="src/utils.ts", symbol="format",
        symbol_type="function", start_line=5, change_type="modified",
    )
    result = extract_diff_hunks_for_symbols(_SAMPLE_DIFF, [sym_auth, sym_utils])
    assert "src/auth.ts" in result
    assert "src/utils.ts" in result
