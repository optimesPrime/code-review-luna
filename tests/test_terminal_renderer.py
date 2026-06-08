import pytest
from unittest.mock import MagicMock


def _make_report(blast=(), quality=(), backend=()):
    from reporter import ReviewReport
    r = ReviewReport(timestamp="2026-01-01", diff_summary="test")
    r.blast_radius_items = list(blast)
    r.code_quality_items = list(quality)
    r.backend_review_items = list(backend)
    return r


def _blast(risk, reason="", needs_human_review=False):
    from phases.blast_radius import BlastRadiusItem
    return BlastRadiusItem(
        risk=risk, file="f.ts", line=1, symbol="x",
        reason=reason, suggestion="fix",
        needs_human_review=needs_human_review,
        confidence="medium",
    )


def _quality(risk, issue_type="logic_gap"):
    from phases.code_quality import CodeQualityItem
    return CodeQualityItem(
        risk=risk, file="f.ts", line=1,
        issue_type=issue_type, description="desc", evidence="ev", suggestion="fix",
        confidence="medium",
    )


class TestBuildVerdict:
    def test_no_items_returns_ok(self):
        from terminal_renderer import build_verdict
        r = _make_report()
        label, style = build_verdict(r)
        assert label == "可提交"
        assert "green" in style

    def test_medium_only_returns_watch(self):
        from terminal_renderer import build_verdict
        r = _make_report(blast=[_blast("medium")])
        label, _ = build_verdict(r)
        assert label == "可提交但建议关注"

    def test_high_returns_fix(self):
        from terminal_renderer import build_verdict
        r = _make_report(blast=[_blast("high", reason="store update")])
        label, _ = build_verdict(r)
        assert label == "建议修复后提交"

    def test_high_with_auth_keyword_no_human_review_returns_block(self):
        from terminal_renderer import build_verdict
        r = _make_report(blast=[_blast("high", reason="auth token missing", needs_human_review=False)])
        label, _ = build_verdict(r)
        assert label == "阻塞提交"

    def test_high_with_auth_keyword_but_needs_human_review_returns_fix(self):
        from terminal_renderer import build_verdict
        r = _make_report(blast=[_blast("high", reason="auth token missing", needs_human_review=True)])
        label, _ = build_verdict(r)
        assert label == "建议修复后提交"


class TestCountRisks:
    def test_counts_across_all_types(self):
        from terminal_renderer import _count_risks
        r = _make_report(
            blast=[_blast("high"), _blast("medium")],
            quality=[_quality("low"), _quality("medium")],
        )
        high, medium, low = _count_risks(r)
        assert high == 1
        assert medium == 2
        assert low == 1


class TestRenderReview:
    def test_json_mode_is_noop(self):
        from terminal_renderer import render_review
        from runtime_context import RuntimeContext
        r = _make_report()
        rt = RuntimeContext()
        # Should not raise even without Rich
        render_review(r, rt, fmt="json")

    def test_render_plain_does_not_raise(self):
        from terminal_renderer import _render_plain
        from runtime_context import RuntimeContext
        r = _make_report(blast=[_blast("high")])
        rt = RuntimeContext(project_name="myapp", changed_files=3, changed_lines=42)
        import io, sys
        old_stderr = sys.stderr
        sys.stderr = io.StringIO()
        try:
            _render_plain(r, rt, quiet=False)
            output = sys.stderr.getvalue()
        finally:
            sys.stderr = old_stderr
        assert "Luna Review" in output
        assert "阻塞提交" in output or "建议修复后提交" in output or "可提交" in output
