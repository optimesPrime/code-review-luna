import sys
import pytest
from unittest.mock import patch, MagicMock
from click.testing import CliRunner
from luna import cli


def _make_diff():
    return "diff --git a/foo.js b/foo.js\n+const x = 1;\n"


def _mock_review_items():
    from phases.blast_radius import BlastRadiusItem
    return [BlastRadiusItem(
        risk="high", file="foo.js", line=1,
        symbol="x", reason="test reason", confidence="high",
        suggestion="fix suggestion", needs_human_review=False
    )]


@patch("luna.save")
@patch("luna.quality.analyze", return_value=[])
@patch("luna.blast.analyze")
@patch("luna.get_diff")
@patch("luna.load_config")
def test_default_mode_does_not_call_ask(mock_cfg, mock_diff, mock_blast, mock_quality, mock_save):
    mock_diff.return_value = _make_diff()
    mock_blast.return_value = _mock_review_items()
    mock_cfg.return_value = MagicMock(
        review=MagicMock(max_diff_chars=100000, apply_enabled=False),
        privacy=MagicMock(redact_patterns=[]),
        skills=MagicMock(),
        reports=MagicMock(output_dir="/tmp"),
        backend=MagicMock(languages=[]),
    )
    mock_save.return_value = "/tmp/report.md"

    runner = CliRunner()
    with patch("luna.ask") as mock_ask:
        result = runner.invoke(cli, ["--staged"])
        mock_ask.assert_not_called()


@patch("luna.save")
@patch("luna.quality.analyze", return_value=[])
@patch("luna.blast.analyze")
@patch("luna.get_diff")
@patch("luna.load_config")
def test_apply_in_non_interactive_exits_with_error(mock_cfg, mock_diff, mock_blast, mock_quality, mock_save):
    mock_diff.return_value = _make_diff()
    mock_blast.return_value = []
    mock_cfg.return_value = MagicMock(
        review=MagicMock(max_diff_chars=100000, apply_enabled=False),
        privacy=MagicMock(redact_patterns=[]),
        skills=MagicMock(),
        reports=MagicMock(output_dir="/tmp"),
        backend=MagicMock(languages=[]),
    )

    runner = CliRunner()
    result = runner.invoke(cli, ["--staged", "--apply"])
    assert result.exit_code == 1
    assert "luna fix" in result.output or "luna fix" in (result.stderr or "")
