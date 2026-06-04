import pytest
from unittest.mock import patch, MagicMock
from diff_reader import get_diff, redact, DiffError


def test_redact_bearer_token():
    text = "Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.abc123"
    result = redact(text, [r"Bearer\s+[A-Za-z0-9._\-]+"])
    assert "eyJhbGciOiJIUzI1NiJ9" not in result
    assert "[REDACTED]" in result


def test_redact_aws_key():
    text = "key = AKIAIOSFODNN7EXAMPLE"
    result = redact(text, [r"AKIA[0-9A-Z]{16}"])
    assert "AKIAIOSFODNN7EXAMPLE" not in result
    assert "[REDACTED]" in result


def test_redact_no_match():
    text = "normal code here"
    result = redact(text, [r"Bearer\s+[A-Za-z0-9._\-]+"])
    assert result == "normal code here"


def test_get_diff_not_git_repo():
    with patch("diff_reader.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=128, stdout="")
        with pytest.raises(DiffError, match="git 仓库"):
            get_diff()


def test_get_diff_staged():
    with patch("diff_reader.subprocess.run") as mock_run:
        check = MagicMock(returncode=0)
        diff_result = MagicMock(returncode=0, stdout="diff --git a/foo.js b/foo.js\n")
        mock_run.side_effect = [check, diff_result]
        result = get_diff(staged=True)
        assert "diff --git" in result
        args = mock_run.call_args_list[1][0][0]
        assert "--cached" in args


def test_get_diff_since():
    with patch("diff_reader.subprocess.run") as mock_run:
        check = MagicMock(returncode=0)
        diff_result = MagicMock(returncode=0, stdout="diff --git a/foo.js b/foo.js\n")
        mock_run.side_effect = [check, diff_result]
        get_diff(since="main")
        args = mock_run.call_args_list[1][0][0]
        assert "main" in args
