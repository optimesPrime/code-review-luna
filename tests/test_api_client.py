from unittest.mock import patch, MagicMock
from config import Config, APIConfig
from api_client import call_claude


def test_call_claude_returns_text(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    cfg = Config()
    mock_response = MagicMock()
    mock_response.content = [MagicMock(text="审查结果: 代码看起来没问题")]
    with patch("api_client.Anthropic") as MockClient:
        MockClient.return_value.messages.create.return_value = mock_response
        result = call_claude("system prompt", "user prompt", cfg)
    assert result == "审查结果: 代码看起来没问题"


def test_call_claude_passes_model(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    cfg = Config()
    cfg.api = APIConfig(model="claude-opus-4-8")
    mock_response = MagicMock()
    mock_response.content = [MagicMock(text="ok")]
    with patch("api_client.Anthropic") as MockClient:
        MockClient.return_value.messages.create.return_value = mock_response
        call_claude("sys", "usr", cfg)
        call_args = MockClient.return_value.messages.create.call_args
        assert call_args.kwargs["model"] == "claude-opus-4-8"
