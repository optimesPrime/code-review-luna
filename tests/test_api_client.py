from unittest.mock import patch, MagicMock
from config import Config, APIConfig
from api_client import call_claude


def _openai_cfg(**kwargs) -> Config:
    cfg = Config()
    cfg.api = APIConfig(provider="openai", key="sk-test", **kwargs)
    return cfg


def _anthropic_cfg(**kwargs) -> Config:
    cfg = Config()
    cfg.api = APIConfig(provider="anthropic", key="sk-ant-test", **kwargs)
    return cfg


# ── 直连 OpenAI ───────────────────────────────────────────────────────────────

def test_openai_returns_text():
    cfg = _openai_cfg(model="gpt-4o")
    mock_resp = MagicMock()
    mock_resp.choices[0].message.content = "审查结果"
    with patch("api_client.OpenAI") as MockClient:
        MockClient.return_value.chat.completions.create.return_value = mock_resp
        result = call_claude("sys", "user", cfg)
    assert result == "审查结果"


def test_openai_passes_model():
    cfg = _openai_cfg(model="gpt-4o")
    mock_resp = MagicMock()
    mock_resp.choices[0].message.content = "ok"
    with patch("api_client.OpenAI") as MockClient:
        MockClient.return_value.chat.completions.create.return_value = mock_resp
        call_claude("sys", "usr", cfg)
    call_args = MockClient.return_value.chat.completions.create.call_args
    assert call_args.kwargs["model"] == "gpt-4o"


def test_openai_no_base_url_kwarg():
    cfg = _openai_cfg(model="gpt-4o", base_url="")
    mock_resp = MagicMock()
    mock_resp.choices[0].message.content = "ok"
    with patch("api_client.OpenAI") as MockClient:
        MockClient.return_value.chat.completions.create.return_value = mock_resp
        call_claude("sys", "usr", cfg)
    _, kwargs = MockClient.call_args
    assert "base_url" not in kwargs


# ── 直连 Anthropic ────────────────────────────────────────────────────────────

def test_anthropic_routes_to_anthropic_client():
    cfg = _anthropic_cfg(model="claude-sonnet-4-6")
    mock_resp = MagicMock()
    mock_resp.content[0].text = "claude 回答"
    with patch("api_client.Anthropic") as MockAnthropic:
        MockAnthropic.return_value.messages.create.return_value = mock_resp
        result = call_claude("sys", "user", cfg)
    MockAnthropic.assert_called_once()
    assert result == "claude 回答"


def test_anthropic_passes_model():
    cfg = _anthropic_cfg(model="claude-opus-4-8")
    mock_resp = MagicMock()
    mock_resp.content[0].text = "ok"
    with patch("api_client.Anthropic") as MockAnthropic:
        MockAnthropic.return_value.messages.create.return_value = mock_resp
        call_claude("sys", "usr", cfg)
    call_args = MockAnthropic.return_value.messages.create.call_args
    assert call_args.kwargs["model"] == "claude-opus-4-8"


# ── 中转站 ────────────────────────────────────────────────────────────────────

def test_proxy_uses_openai_client_with_base_url():
    cfg = _anthropic_cfg(model="claude-sonnet-4-6", base_url="https://my-proxy.com/v1")
    mock_resp = MagicMock()
    mock_resp.choices[0].message.content = "ok"
    with patch("api_client.OpenAI") as MockOpenAI:
        MockOpenAI.return_value.chat.completions.create.return_value = mock_resp
        call_claude("sys", "usr", cfg)
    _, kwargs = MockOpenAI.call_args
    assert kwargs["base_url"] == "https://my-proxy.com/v1"


def test_proxy_overrides_anthropic_provider():
    """base_url 优先于 provider=anthropic，走中转站而非直连 Anthropic。"""
    cfg = _anthropic_cfg(model="claude-sonnet-4-6", base_url="https://proxy.com/v1")
    mock_resp = MagicMock()
    mock_resp.choices[0].message.content = "ok"
    with patch("api_client.Anthropic") as MockAnthropic, \
         patch("api_client.OpenAI") as MockOpenAI:
        MockOpenAI.return_value.chat.completions.create.return_value = mock_resp
        call_claude("sys", "usr", cfg)
    MockAnthropic.assert_not_called()
    MockOpenAI.assert_called_once()
