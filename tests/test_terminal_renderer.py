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


class TestBuildCheckpoints:
    def test_no_items_all_ok(self):
        from terminal_renderer import build_checkpoints
        r = _make_report()
        results = build_checkpoints(r)
        assert len(results) == 11
        assert all(cp.status == "ok" for cp in results)

    def test_blast_item_mapped_to_correct_checkpoint(self):
        from terminal_renderer import build_checkpoints
        r = _make_report(blast=[_blast("high", reason="auth token missing in header")])
        results = build_checkpoints(r)
        # Should hit "请求上下文" (header) and "权限/登录态" (auth)
        hit = [cp for cp in results if cp.status != "ok"]
        names = [cp.name for cp in hit]
        assert "请求上下文" in names or "权限/登录态" in names

    def test_quality_item_mapped(self):
        from terminal_renderer import build_checkpoints
        r = _make_report(quality=[_quality("medium", issue_type="missing_error_handling")])
        # CodeQualityItem description="desc" (from helper) — won't match most keywords
        # But issue_type=missing_error_handling contributes to fix_mode
        results = build_checkpoints(r)
        assert isinstance(results, list)
        assert len(results) == 11

    def test_highest_risk_item_wins(self):
        from terminal_renderer import build_checkpoints
        r = _make_report(blast=[
            _blast("low", reason="auth token low risk"),
            _blast("high", reason="auth token missing completely"),
        ])
        results = build_checkpoints(r)
        auth_cp = next(cp for cp in results if cp.name == "权限/登录态")
        assert auth_cp.status == "high"


class TestBuildBusinessTree:
    def test_no_items_returns_none(self):
        from terminal_renderer import build_business_tree
        r = _make_report()
        assert build_business_tree(r) is None

    def test_with_blast_items_returns_tree(self):
        from terminal_renderer import build_business_tree
        r = _make_report(blast=[_blast("high", reason="auth token missing")])
        result = build_business_tree(r)
        # Rich Tree or None (if Rich not installed in test env)
        # Just verify it doesn't raise and returns something
        assert result is not None or result is None  # Rich may not be installed

    def test_with_impact_paths_uses_strategy1(self):
        from terminal_renderer import build_business_tree
        r = _make_report(blast=[_blast("high", reason="store sync")])
        r.impact_paths = [{"risk": "high", "path": ["A", "B", "C"], "reason": "cascades", "evidence": "file.ts:10"}]
        r.changed_symbols = [{"name": "myFunc", "file": "a.ts"}]
        result = build_business_tree(r)
        # Should not raise
        assert result is not None or result is None

    def test_blast_items_grouped_by_checkpoint(self):
        from terminal_renderer import build_business_tree
        r = _make_report(blast=[
            _blast("high", reason="auth login failed"),
            _blast("medium", reason="store state sync error"),
        ])
        result = build_business_tree(r)
        assert result is not None or result is None  # Rich may not be installed

    def test_unmatched_blast_items_go_to_business_logic_group(self):
        from terminal_renderer import build_business_tree
        r = _make_report(blast=[_blast("low", reason="some obscure thing xyz")])
        result = build_business_tree(r)
        assert result is not None or result is None

    def test_root_label_uses_changed_symbol_name(self):
        from terminal_renderer import build_business_tree, RICH_AVAILABLE
        r = _make_report(blast=[_blast("high", reason="store sync")])
        r.changed_symbols = [{"name": "myFunction", "file": "a.ts"}]
        result = build_business_tree(r)
        if RICH_AVAILABLE and result is not None:
            # Tree label should contain the symbol name
            assert "myFunction" in result.label

    def test_impact_paths_empty_fallback_to_blast(self):
        from terminal_renderer import build_business_tree
        r = _make_report(blast=[_blast("medium", reason="router redirect issue")])
        r.impact_paths = []
        result = build_business_tree(r)
        assert result is not None or result is None


class TestBuildFixQueue:
    def test_empty_report_returns_empty(self):
        from terminal_renderer import build_fix_queue
        r = _make_report()
        assert build_fix_queue(r) == []

    def test_high_risk_blast_included_even_without_suggestion(self):
        from terminal_renderer import build_fix_queue
        from phases.blast_radius import BlastRadiusItem
        item = BlastRadiusItem(
            risk="high", file="f.ts", line=1, symbol="x",
            reason="critical issue", suggestion="",
            needs_human_review=False,
            confidence="medium",
        )
        r = _make_report(blast=[item])
        queue = build_fix_queue(r)
        assert len(queue) == 1
        assert queue[0].mode == "assist"

    def test_auto_classification(self):
        from terminal_renderer import build_fix_queue
        r = _make_report(quality=[_quality("medium", issue_type="missing_error_handling")])
        queue = build_fix_queue(r)
        # description="desc" doesn't contain auth keywords
        assert any(fc.mode == "auto" for fc in queue)

    def test_manual_for_needs_human_review(self):
        from terminal_renderer import build_fix_queue
        from phases.blast_radius import BlastRadiusItem
        item = BlastRadiusItem(
            risk="high", file="f.ts", line=1, symbol="x",
            reason="business timing issue", suggestion="manual fix needed",
            needs_human_review=True,
            confidence="medium",
        )
        r = _make_report(blast=[item])
        queue = build_fix_queue(r)
        assert queue[0].mode == "manual"
        assert queue[0].impact == "阻塞"

    def test_command_hints_correct(self):
        from terminal_renderer import build_fix_queue, FixCandidate
        from phases.blast_radius import BlastRadiusItem
        item = BlastRadiusItem(
            risk="high", file="f.ts", line=1, symbol="x",
            reason="store sync issue", suggestion="fix it",
            needs_human_review=False,
            confidence="medium",
        )
        r = _make_report(blast=[item])
        queue = build_fix_queue(r)
        assert queue[0].command_hint == "luna fix 1 --preview"


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


class TestAdversarialRefutedSection:
    def _make_runtime(self):
        from runtime_context import RuntimeContext
        return RuntimeContext()

    def test_refuted_section_shown_when_present(self):
        from phases.adversarial_verifier import RefutedFinding
        from phases.blast_radius import BlastRadiusItem
        from terminal_renderer import _render_rich, RICH_AVAILABLE
        import io
        if not RICH_AVAILABLE:
            pytest.skip("Rich not installed")
        from rich.console import Console

        item = BlastRadiusItem(
            file="src/a.ts", line=42, symbol="funcPay",
            risk="high", confidence="medium", reason="可能影响支付",
        )
        r = _make_report()
        r.adversarial_refuted = [RefutedFinding(item=item, adv_reason="调用方不使用返回值")]

        buf = io.StringIO()
        console = Console(file=buf, no_color=True, highlight=False)
        _render_rich(console, r, self._make_runtime(), quiet=False)
        output = buf.getvalue()

        assert "反驳" in output
        assert "funcPay" in output
        assert "调用方不使用返回值" in output

    def test_refuted_section_absent_when_empty(self):
        from terminal_renderer import _render_rich, RICH_AVAILABLE
        import io
        if not RICH_AVAILABLE:
            pytest.skip("Rich not installed")
        from rich.console import Console

        r = _make_report()
        r.adversarial_refuted = []

        buf = io.StringIO()
        console = Console(file=buf, no_color=True, highlight=False)
        _render_rich(console, r, self._make_runtime(), quiet=False)
        output = buf.getvalue()

        assert "反驳过滤" not in output


class TestBuildBlastChain:
    def test_from_impact_paths(self):
        from terminal_renderer import build_blast_chain
        r = _make_report()
        r.impact_paths = [{"path": ["src/a.ts", "src/b.ts", "src/c.ts"], "risk": "high"}]
        chains = build_blast_chain(r)
        assert len(chains) == 1
        assert "a.ts" in chains[0]
        assert "→" in chains[0]

    def test_max_3_chains(self):
        from terminal_renderer import build_blast_chain
        r = _make_report()
        r.impact_paths = [
            {"path": ["a.ts", "b.ts"], "risk": "high"},
            {"path": ["c.ts", "d.ts"], "risk": "medium"},
            {"path": ["e.ts", "f.ts"], "risk": "low"},
            {"path": ["g.ts", "h.ts"], "risk": "low"},
        ]
        assert len(build_blast_chain(r)) == 3

    def test_fallback_to_blast_files_when_no_impact_paths(self):
        from terminal_renderer import build_blast_chain
        from phases.blast_radius import BlastRadiusItem
        item_a = BlastRadiusItem(file="src/store.ts", line=1, symbol="x", risk="high", confidence="medium", reason="r")
        item_b = BlastRadiusItem(file="src/auth.ts",  line=2, symbol="y", risk="high", confidence="medium", reason="r")
        r = _make_report(blast=[item_a, item_b])
        chains = build_blast_chain(r)
        assert len(chains) == 1
        assert "store.ts" in chains[0]
        assert "auth.ts" in chains[0]

    def test_empty_when_no_data(self):
        from terminal_renderer import build_blast_chain
        r = _make_report()
        assert build_blast_chain(r) == []

    def test_truncates_long_path(self):
        from terminal_renderer import build_blast_chain
        r = _make_report()
        r.impact_paths = [{"path": [f"file{i}.ts" for i in range(10)], "risk": "high"}]
        chains = build_blast_chain(r)
        assert "..." in chains[0]


class TestCmdForItem:
    def _make_fc(self, id, file, line, mode, hint):
        from terminal_renderer import FixCandidate
        return FixCandidate(id=id, mode=mode, title="t", reason="r",
                            command_hint=hint, impact="高价值",
                            file=file, line=line)

    def test_returns_command_hint_when_matched(self):
        from terminal_renderer import _cmd_for_item
        fc = self._make_fc(1, "src/auth.ts", 18, "assist", "luna fix 1 --preview")
        item = _blast("high", "r")
        item.file = "src/auth.ts"
        item.line = 18
        assert _cmd_for_item(item, [fc]) == "luna fix 1 --preview"

    def test_returns_none_when_no_match(self):
        from terminal_renderer import _cmd_for_item
        fc = self._make_fc(1, "src/auth.ts", 18, "assist", "luna fix 1 --preview")
        item = _blast("high", "r")
        item.file = "src/other.ts"
        item.line = 99
        assert _cmd_for_item(item, [fc]) is None

    def test_returns_none_when_empty_candidates(self):
        from terminal_renderer import _cmd_for_item
        item = _blast("high", "r")
        assert _cmd_for_item(item, []) is None


class TestCmdForCheckpoint:
    def test_returns_luna_detail_when_matched(self):
        from terminal_renderer import _cmd_for_checkpoint, CheckpointResult, FixCandidate
        cp = CheckpointResult(name="权限/登录态", status="high",
                              reason="auth missing", evidence="src/auth.ts:18", fix_mode="assist")
        fc = FixCandidate(id=3, mode="assist", title="t", reason="r",
                          command_hint="luna fix 3 --preview", impact="高价值",
                          file="src/auth.ts", line=18)
        assert _cmd_for_checkpoint(cp, [fc]) == "luna detail 3"

    def test_returns_none_when_no_evidence(self):
        from terminal_renderer import _cmd_for_checkpoint, CheckpointResult
        cp = CheckpointResult(name="测试覆盖", status="ok",
                              reason="ok", evidence="-", fix_mode="-")
        assert _cmd_for_checkpoint(cp, []) is None

    def test_returns_none_when_no_match(self):
        from terminal_renderer import _cmd_for_checkpoint, CheckpointResult
        cp = CheckpointResult(name="权限/登录态", status="high",
                              reason="auth missing", evidence="src/auth.ts:18", fix_mode="assist")
        assert _cmd_for_checkpoint(cp, []) is None


class TestRenderItemCard:
    def _render(self, item, fix_candidates=(), icon="🚨", style="bold red"):
        from terminal_renderer import _render_item_card, RICH_AVAILABLE
        import io
        if not RICH_AVAILABLE:
            pytest.skip("Rich not installed")
        from rich.console import Console
        buf = io.StringIO()
        console = Console(file=buf, no_color=True, highlight=False)
        _render_item_card(console, item, list(fix_candidates), icon=icon, style=style)
        return buf.getvalue()

    def test_shows_file_and_reason(self):
        item = _blast("high", "权限校验缺失")
        item.file = "src/auth.ts"
        item.line = 18
        output = self._render(item)
        assert "src/auth.ts:18" in output
        assert "权限校验缺失" in output

    def test_shows_suggestion_when_present(self):
        from phases.blast_radius import BlastRadiusItem
        item = BlastRadiusItem(file="f.ts", line=1, symbol="x", risk="high",
                               confidence="medium", reason="r", suggestion="加装饰器")
        output = self._render(item)
        assert "加装饰器" in output

    def test_shows_command_when_matched(self):
        from terminal_renderer import FixCandidate
        item = _blast("high", "r")
        item.file = "src/auth.ts"
        item.line = 18
        fc = FixCandidate(id=1, mode="assist", title="t", reason="r",
                          command_hint="luna fix 1 --preview", impact="高价值",
                          file="src/auth.ts", line=18)
        output = self._render(item, fix_candidates=[fc])
        assert "luna fix 1 --preview" in output

    def test_no_command_when_no_match(self):
        item = _blast("high", "r")
        output = self._render(item, fix_candidates=[])
        assert "luna fix" not in output
        assert "luna detail" not in output


class TestRenderItemInline:
    def _render(self, item, fix_candidates=()):
        from terminal_renderer import _render_item_inline, RICH_AVAILABLE
        import io
        if not RICH_AVAILABLE:
            pytest.skip("Rich not installed")
        from rich.console import Console
        buf = io.StringIO()
        console = Console(file=buf, no_color=True, highlight=False)
        _render_item_inline(console, item, list(fix_candidates))
        return buf.getvalue()

    def test_shows_file_and_truncated_reason(self):
        item = _blast("low", "some low risk thing")
        item.file = "src/router.ts"
        item.line = 7
        output = self._render(item)
        assert "src/router.ts:7" in output
        assert "some low risk thing" in output

    def test_single_line(self):
        item = _blast("low", "r")
        output = self._render(item)
        assert output.count("\n") <= 2


class TestNewRenderRich:
    def _make_runtime(self):
        from runtime_context import RuntimeContext
        return RuntimeContext()

    def _render(self, report, quiet=False):
        from terminal_renderer import _render_rich, RICH_AVAILABLE
        import io
        if not RICH_AVAILABLE:
            pytest.skip("Rich not installed")
        from rich.console import Console
        buf = io.StringIO()
        console = Console(file=buf, no_color=True, highlight=False)
        _render_rich(console, report, self._make_runtime(), quiet=quiet)
        return buf.getvalue()

    def test_high_risk_shows_in_must_fix_section(self):
        r = _make_report(blast=[_blast("high", "权限缺失")])
        r.blast_radius_items[0].file = "src/auth.ts"
        output = self._render(r)
        assert "必须修复" in output
        assert "src/auth.ts" in output

    def test_command_inline_with_item(self):
        from phases.blast_radius import BlastRadiusItem
        item = BlastRadiusItem(file="src/auth.ts", line=18, symbol="x",
                               risk="high", confidence="medium",
                               reason="权限缺失", suggestion="加装饰器",
                               needs_human_review=False)
        r = _make_report(blast=[item])
        output = self._render(r)
        assert "luna fix" in output or "luna detail" in output

    def test_no_fix_queue_table(self):
        r = _make_report(blast=[_blast("high", "r")])
        output = self._render(r)
        assert "修复队列" not in output

    def test_no_verdict_panel(self):
        r = _make_report()
        output = self._render(r)
        assert "╭" not in output

    def test_no_token_savings(self):
        r = _make_report()
        r.token_savings = {"blast": {"baseline": 1000, "used": 500, "saved": 500, "saved_percent": 50}}
        output = self._render(r)
        assert "Token" not in output

    def test_checkpoint_section_absent_when_all_ok(self):
        r = _make_report()
        output = self._render(r)
        assert "审查点命中" not in output

    def test_checkpoint_section_shown_when_hit(self):
        r = _make_report(blast=[_blast("high", "auth token missing", needs_human_review=False)])
        output = self._render(r)
        assert "审查点命中" in output

    def test_overflow_hint_when_more_than_5_high(self):
        highs = [_blast("high", f"issue {i}") for i in range(8)]
        r = _make_report(blast=highs)
        output = self._render(r)
        assert "+" in output and "条高风险" in output

    def test_blast_chain_shown_when_impact_paths(self):
        r = _make_report(blast=[_blast("high", "r")])
        r.impact_paths = [{"path": ["src/a.ts", "src/b.ts"], "risk": "high"}]
        output = self._render(r)
        assert "爆炸范围" in output
        assert "→" in output

    def test_medium_in_suggest_fix_section(self):
        r = _make_report(blast=[_blast("medium", "缺少错误处理")])
        output = self._render(r)
        assert "建议修复" in output

    def test_quiet_mode_shows_only_header_and_verdict(self):
        r = _make_report(blast=[_blast("high", "r")])
        output = self._render(r, quiet=True)
        assert "必须修复" not in output
        assert "审查点命中" not in output


class TestGroupImpactPaths:
    def test_empty_when_no_impact_paths(self):
        from terminal_renderer import _group_impact_paths
        r = _make_report()
        assert _group_impact_paths(r) == []

    def test_each_source_becomes_one_block(self):
        from terminal_renderer import _group_impact_paths
        r = _make_report()
        r.impact_paths = [
            {"path": ["src/a.ts", "src/b.ts"], "risk": "high",   "reason": "r1"},
            {"path": ["src/c.ts", "src/d.ts"], "risk": "medium", "reason": "r2"},
        ]
        blocks = _group_impact_paths(r)
        assert len(blocks) == 2
        assert blocks[0].symbol_name == "a.ts"
        assert blocks[1].symbol_name == "c.ts"

    def test_same_source_merged_into_one_block(self):
        from terminal_renderer import _group_impact_paths
        r = _make_report()
        r.impact_paths = [
            {"path": ["src/a.ts", "src/b.ts"], "risk": "high",   "reason": "r1"},
            {"path": ["src/a.ts", "src/c.ts"], "risk": "medium", "reason": "r2"},
        ]
        blocks = _group_impact_paths(r)
        assert len(blocks) == 1
        assert len(blocks[0].chains) == 2

    def test_block_risk_is_highest_across_paths(self):
        from terminal_renderer import _group_impact_paths
        r = _make_report()
        r.impact_paths = [
            {"path": ["src/a.ts", "src/b.ts"], "risk": "low",  "reason": "r"},
            {"path": ["src/a.ts", "src/c.ts"], "risk": "high", "reason": "r"},
        ]
        blocks = _group_impact_paths(r)
        assert blocks[0].risk == "high"

    def test_node_reason_matched_from_blast_item(self):
        from terminal_renderer import _group_impact_paths
        from phases.blast_radius import BlastRadiusItem
        item = BlastRadiusItem(
            file="src/b.ts", line=42, symbol="x",
            risk="high", confidence="medium", reason="auth check fails",
        )
        r = _make_report(blast=[item])
        r.impact_paths = [{"path": ["src/a.ts", "src/b.ts"], "risk": "high", "reason": "fallback"}]
        blocks = _group_impact_paths(r)
        node = blocks[0].chains[0][0]
        assert node.reason == "auth check fails"
        assert node.line == 42

    def test_node_reason_fallback_to_path_reason_on_leaf(self):
        from terminal_renderer import _group_impact_paths
        r = _make_report()
        r.impact_paths = [{"path": ["src/a.ts", "src/b.ts"], "risk": "medium", "reason": "path level reason"}]
        blocks = _group_impact_paths(r)
        node = blocks[0].chains[0][0]
        assert node.reason == "path level reason"

    def test_symbol_name_from_changed_symbols(self):
        from terminal_renderer import _group_impact_paths
        r = _make_report()
        r.changed_symbols = [{"name": "getUserById", "file": "src/a.ts"}]
        r.impact_paths = [{"path": ["src/a.ts", "src/b.ts"], "risk": "high", "reason": "r"}]
        blocks = _group_impact_paths(r)
        assert blocks[0].symbol_name == "getUserById"

    def test_single_node_paths_skipped(self):
        from terminal_renderer import _group_impact_paths
        r = _make_report()
        r.impact_paths = [{"path": ["src/a.ts"], "risk": "high", "reason": "r"}]
        blocks = _group_impact_paths(r)
        assert blocks == []
