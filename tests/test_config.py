import pytest
import yaml
from config import load_config, APIConfig


def test_defaults_when_no_file():
    cfg = load_config("nonexistent.yaml")
    assert cfg.api.provider == "anthropic"
    assert cfg.api.model == "claude-sonnet-4-6"
    assert cfg.review.max_diff_chars == 120_000
    assert cfg.review.apply_enabled is False
    assert cfg.review.confirm_before_fix is True


def test_loads_from_yaml(tmp_path):
    f = tmp_path / "config.yaml"
    f.write_text(yaml.dump({
        "api": {"provider": "openai", "model": "gpt-4o", "api_key_env": "OPENAI_API_KEY"},
        "review": {"language": "en", "max_diff_chars": 50000},
    }))
    cfg = load_config(str(f))
    assert cfg.api.provider == "openai"
    assert cfg.api.model == "gpt-4o"
    assert cfg.review.language == "en"
    assert cfg.review.max_diff_chars == 50000


def test_api_key_from_env(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-123")
    cfg = load_config("nonexistent.yaml")
    assert cfg.api.api_key == "test-key-123"


def test_api_key_missing_raises(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    cfg = load_config("nonexistent.yaml")
    with pytest.raises(ValueError, match="ANTHROPIC_API_KEY"):
        _ = cfg.api.api_key


def test_skills_loaded(tmp_path):
    f = tmp_path / "config.yaml"
    f.write_text(yaml.dump({
        "skills": [{"name": "vue-patterns", "path": "./skills/vue.md"}]
    }))
    cfg = load_config(str(f))
    assert len(cfg.skills) == 1
    assert cfg.skills[0].name == "vue-patterns"


def test_privacy_redact_patterns(tmp_path):
    f = tmp_path / "config.yaml"
    f.write_text(yaml.dump({
        "privacy": {"redact_patterns": ["SECRET\\d+"]}
    }))
    cfg = load_config(str(f))
    assert "SECRET\\d+" in cfg.privacy.redact_patterns


def test_backend_config_loaded(tmp_path):
    f = tmp_path / "config.yaml"
    f.write_text("""
review:
  project_type: backend
backend:
  languages:
    - csharp
    - java
    - python
    - nodejs
    - go
    - php
    - cpp
  enabled: true
  max_depth: 4
""", encoding="utf-8")

    cfg = load_config(str(f))

    assert cfg.review.project_type == "backend"
    assert cfg.backend.enabled is True
    assert cfg.backend.languages == ["csharp", "java", "python", "nodejs", "go", "php", "cpp"]
    assert cfg.backend.max_depth == 4
