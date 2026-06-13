from __future__ import annotations
import os
from dataclasses import dataclass, field
from pathlib import Path
import yaml


@dataclass
class APIConfig:
    provider: str = "anthropic"
    model: str = "claude-sonnet-4-6"
    api_key_env: str = "ANTHROPIC_API_KEY"
    base_url: str = ""
    key: str = ""  # 直接存储的 API key，由 luna switch 写入

    @property
    def api_key(self) -> str:
        if self.key.strip():
            return self.key.strip()
        env_key = os.environ.get(self.api_key_env, "")
        if not env_key:
            raise ValueError(f"未配置 API Key，请运行 luna switch")
        return env_key


@dataclass
class ReviewConfig:
    language: str = "zh"
    project_type: str = "auto"
    confirm_before_fix: bool = True
    max_diff_chars: int = 120_000
    apply_enabled: bool = False


@dataclass
class SkillEntry:
    name: str
    path: str


@dataclass
class ReportsConfig:
    output_dir: str = "./.luna-reports"


@dataclass
class PrivacyConfig:
    ignore: list[str] = field(default_factory=lambda: [
        ".env", ".env.*", "node_modules/", "dist/"
    ])
    redact_patterns: list[str] = field(default_factory=lambda: [
        r"Bearer\s+[A-Za-z0-9._\-]+",
        r"AKIA[0-9A-Z]{16}",
    ])


@dataclass
class BackendConfig:
    enabled: bool = True
    languages: list[str] = field(default_factory=lambda: ["csharp", "java", "python", "nodejs", "go", "php", "cpp"])
    max_depth: int = 4


@dataclass
class MigrationConfig:
    enabled: bool = True
    use_llm: bool = False


@dataclass
class APIChangeConfig:
    enabled: bool = True


@dataclass
class GitLabConfig:
    url: str = "https://gitlab.com"
    token_env: str = "GITLAB_TOKEN"
    token: str = ""            # 直接存储的 token，由 luna gitlab 写入
    project_id: str = ""
    bot_note_prefix: str = "🌙 Luna Review"
    post_inline: bool = True
    min_risk: str = "medium"   # "high" | "medium" | "low"


@dataclass
class Config:
    api: APIConfig = field(default_factory=APIConfig)
    review: ReviewConfig = field(default_factory=ReviewConfig)
    skills: list[SkillEntry] = field(default_factory=list)
    reports: ReportsConfig = field(default_factory=ReportsConfig)
    privacy: PrivacyConfig = field(default_factory=PrivacyConfig)
    backend: BackendConfig = field(default_factory=BackendConfig)
    migration: MigrationConfig = field(default_factory=MigrationConfig)
    api_change: APIChangeConfig = field(default_factory=APIChangeConfig)
    gitlab: GitLabConfig = field(default_factory=GitLabConfig)


def load_config(path: str = "config.yaml") -> Config:
    p = Path(path)
    if not p.exists():
        return Config()

    with p.open(encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}

    cfg = Config()

    if api_raw := raw.get("api"):
        known = APIConfig.__dataclass_fields__.keys()
        cfg.api = APIConfig(**{k: v for k, v in api_raw.items() if k in known})

    if r := raw.get("review"):
        known = ReviewConfig.__dataclass_fields__.keys()
        cfg.review = ReviewConfig(**{k: v for k, v in r.items() if k in known})

    if "skills" in raw:
        skills_raw = raw["skills"] or []
        cfg.skills = [
            SkillEntry(name=s["name"], path=s["path"])
            for s in skills_raw
            if isinstance(s, dict) and "name" in s and "path" in s
        ]

    if rep := raw.get("reports"):
        known = ReportsConfig.__dataclass_fields__.keys()
        cfg.reports = ReportsConfig(**{k: v for k, v in rep.items() if k in known})

    if pv := raw.get("privacy"):
        known = PrivacyConfig.__dataclass_fields__.keys()
        cfg.privacy = PrivacyConfig(**{k: v for k, v in pv.items() if k in known})

    if b := raw.get("backend"):
        known = BackendConfig.__dataclass_fields__.keys()
        cfg.backend = BackendConfig(**{k: v for k, v in b.items() if k in known})

    if gl := raw.get("gitlab"):
        known = GitLabConfig.__dataclass_fields__.keys()
        cfg.gitlab = GitLabConfig(**{k: v for k, v in gl.items() if k in known})

    return cfg
