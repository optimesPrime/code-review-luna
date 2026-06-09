import dataclasses
import json
from pathlib import Path

from terminal_renderer import FixCandidate


def _make_candidate(**kwargs) -> FixCandidate:
    defaults = dict(id=1, mode="auto", title="fix loading", reason="Login.vue:74",
                    command_hint="luna fix 1", impact="高价值",
                    file="src/Login.vue", line=74,
                    evidence="catch 块未恢复 loading", suggestion="加 loading.value = false")
    defaults.update(kwargs)
    return FixCandidate(**defaults)


# ── Task 1 ────────────────────────────────────────────────────────────────────

def test_fix_candidate_has_required_fields():
    """FixCandidate must carry file/line/evidence/suggestion for luna fix to work."""
    fc = FixCandidate(
        id=1,
        mode="auto",
        title="登录失败后恢复 loading",
        reason="src/views/Login.vue:74",
        command_hint="luna fix 1",
        impact="高价值",
        file="src/views/Login.vue",
        line=74,
        evidence="Login.vue:74 catch 块未恢复 loading",
        suggestion="在 catch 块末尾添加 loading.value = false",
    )
    d = dataclasses.asdict(fc)
    assert d["file"] == "src/views/Login.vue"
    assert d["line"] == 74
    assert d["evidence"] == "Login.vue:74 catch 块未恢复 loading"
    assert d["suggestion"] == "在 catch 块末尾添加 loading.value = false"


# ── Task 2 ────────────────────────────────────────────────────────────────────

def test_load_latest_report_returns_none_when_missing(tmp_path):
    from luna_fix import load_latest_report
    assert load_latest_report(str(tmp_path)) is None


def test_load_latest_report_returns_candidates(tmp_path):
    from luna_fix import load_latest_report
    fc = _make_candidate()
    data = {"fix_candidates": [dataclasses.asdict(fc)]}
    (tmp_path / "latest.json").write_text(json.dumps(data), encoding="utf-8")
    result = load_latest_report(str(tmp_path))
    assert result is not None
    assert len(result) == 1
    assert result[0].file == "src/Login.vue"
    assert result[0].line == 74


def test_apply_patch_writes_file(tmp_path):
    from luna_fix import apply_patch
    target = tmp_path / "src" / "Login.vue"
    target.parent.mkdir()
    original = "line1\nline2\nline3\n"
    target.write_text(original, encoding="utf-8")

    patch = (
        "--- a/src/Login.vue\n"
        "+++ b/src/Login.vue\n"
        "@@ -1,3 +1,4 @@\n"
        " line1\n"
        " line2\n"
        "+line2b\n"
        " line3\n"
    )
    result = apply_patch(patch, str(tmp_path))
    assert result is True
    assert "line2b" in target.read_text(encoding="utf-8")


def test_apply_patch_returns_false_for_bad_patch(tmp_path):
    from luna_fix import apply_patch
    (tmp_path / "f.py").write_text("hello\n", encoding="utf-8")
    bad_patch = "--- a/f.py\n+++ b/f.py\n@@ -99,1 +99,2 @@\n missing context\n"
    result = apply_patch(bad_patch, str(tmp_path))
    assert result is False


def test_generate_fix_returns_none_for_manual_mode():
    from luna_fix import generate_fix
    fc = _make_candidate(mode="manual")
    assert generate_fix(fc, "some source", cfg=None) is None


# ── Task 3 ────────────────────────────────────────────────────────────────────

def test_fix_cmd_unknown_id_exits_1(tmp_path):
    from click.testing import CliRunner
    from luna import fix_cmd
    data = {"fix_candidates": [dataclasses.asdict(_make_candidate(id=1))]}
    (tmp_path / "latest.json").write_text(json.dumps(data), encoding="utf-8")
    runner = CliRunner()
    result = runner.invoke(fix_cmd, ["99", "--reports-dir", str(tmp_path)])
    assert result.exit_code == 1


# ── Task 4 ────────────────────────────────────────────────────────────────────

def test_render_diff_preview_outputs_to_stderr(capsys):
    from terminal_renderer import render_diff_preview
    patch = "--- a/foo.py\n+++ b/foo.py\n@@ -1,1 +1,2 @@\n hello\n+world\n"
    render_diff_preview(patch)
    captured = capsys.readouterr()
    # Rich writes to stderr; plain fallback also writes to stderr
    assert "foo.py" in captured.err or "foo.py" in captured.out


def test_fix_queue_command_hint_uses_luna_fix_n():
    from terminal_renderer import build_fix_queue, FixCandidate
    from reporter import ReviewReport
    from phases.blast_radius import BlastRadiusItem

    item = BlastRadiusItem(
        file="src/foo.ts", line=10, symbol="foo", risk="high",
        confidence="high", reason="test reason", suggestion="fix it",
        needs_human_review=False,
    )
    report = ReviewReport(timestamp="2026-01-01", diff_summary="")
    report.blast_radius_items = [item]
    candidates = build_fix_queue(report)
    assert len(candidates) >= 1
    assert "--apply" not in candidates[0].command_hint
    assert candidates[0].command_hint in (f"luna fix {candidates[0].id}", f"luna fix {candidates[0].id} --preview")


def test_fix_cmd_manual_mode_prints_evidence(tmp_path):
    from click.testing import CliRunner
    from luna import fix_cmd
    fc = _make_candidate(id=1, mode="manual", evidence="catch 块未恢复 loading")
    data = {"fix_candidates": [dataclasses.asdict(fc)]}
    (tmp_path / "latest.json").write_text(json.dumps(data), encoding="utf-8")
    runner = CliRunner()
    result = runner.invoke(fix_cmd, ["1", "--reports-dir", str(tmp_path)])
    assert "人工" in result.output or "manual" in result.output.lower()
    assert result.exit_code == 0
