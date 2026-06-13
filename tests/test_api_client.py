from unittest.mock import patch, MagicMock
from config import Config, APIConfig
from api_client import call_claude


def test_call_claude_returns_text(monkeypatch):
    cfg = Config()
    cfg.api = APIConfig(model="gpt-4o", key="sk-test")
    mock_response = MagicMock()
    mock_response.choices[0].message.content = "审查结果: 代码看起来没问题"
    with patch("api_client.OpenAI") as MockClient:
        MockClient.return_value.chat.completions.create.return_value = mock_response
        result = call_claude("system prompt", "user prompt", cfg)
    assert result == "审查结果: 代码看起来没问题"


def test_call_claude_passes_model(monkeypatch):
    cfg = Config()
    cfg.api = APIConfig(model="gpt-4o", key="sk-test")
    mock_response = MagicMock()
    mock_response.choices[0].message.content = "ok"
    with patch("api_client.OpenAI") as MockClient:
        MockClient.return_value.chat.completions.create.return_value = mock_response
        call_claude("sys", "usr", cfg)
        call_args = MockClient.return_value.chat.completions.create.call_args
        assert call_args.kwargs["model"] == "gpt-4o"


def test_call_claude_uses_base_url():
    cfg = Config()
    cfg.api = APIConfig(model="gpt-4o", key="sk-test", base_url="https://my-proxy.com/v1")
    mock_response = MagicMock()
    mock_response.choices[0].message.content = "ok"
    with patch("api_client.OpenAI") as MockClient:
        MockClient.return_value.chat.completions.create.return_value = mock_response
        call_claude("sys", "usr", cfg)
    _, kwargs = MockClient.call_args
    assert kwargs["base_url"] == "https://my-proxy.com/v1"


def test_call_claude_no_base_url_omits_kwarg():
    cfg = Config()
    cfg.api = APIConfig(model="gpt-4o", key="sk-test", base_url="")
    mock_response = MagicMock()
    mock_response.choices[0].message.content = "ok"
    with patch("api_client.OpenAI") as MockClient:
        MockClient.return_value.chat.completions.create.return_value = mock_response
        call_claude("sys", "usr", cfg)
    _, kwargs = MockClient.call_args
    assert "base_url" not in kwargs
