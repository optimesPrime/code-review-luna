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

    @property
    def api_key(self) -> str:
        key = os.environ.get(self.api_key_env, "")
        if not key:
            raise ValueError(f"环境变量 {self.api_key_env} 未设置")
        return key


@dataclass
class ReviewConfig:
    language: str = "zh"
    project_type: str = "frontend"
    confirm_before_fix: bool = True
    max_diff_chars: int = 120_000
    apply_enabled: bool = False


@dataclass
class SkillEntry:
    name: str
    path: str


@dataclass
class ReportsConfig:
    output_dir: str = "./.cr-reports"


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
class Config:
    api: APIConfig = field(default_factory=APIConfig)
    review: ReviewConfig = field(default_factory=ReviewConfig)
    skills: list[SkillEntry] = field(default_factory=list)
    reports: ReportsConfig = field(default_factory=ReportsConfig)
    privacy: PrivacyConfig = field(default_factory=PrivacyConfig)


def load_config(path: str = "config.yaml") -> Config:
    p = Path(path)
    if not p.exists():
        return Config()

    with p.open(encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}

    cfg = Config()

    if api_raw := raw.get("api"):
        cfg.api = APIConfig(
            provider=api_raw.get("provider", "anthropic"),
            model=api_raw.get("model", "claude-sonnet-4-6"),
            api_key_env=api_raw.get("api_key_env", "ANTHROPIC_API_KEY"),
        )

    if r := raw.get("review"):
        cfg.review = ReviewConfig(
            language=r.get("language", "zh"),
            project_type=r.get("project_type", "frontend"),
            confirm_before_fix=r.get("confirm_before_fix", True),
            max_diff_chars=r.get("max_diff_chars", 120_000),
            apply_enabled=r.get("apply_enabled", False),
        )

    if skills_raw := raw.get("skills"):
        cfg.skills = [SkillEntry(name=s["name"], path=s["path"]) for s in skills_raw]

    if rep := raw.get("reports"):
        cfg.reports = ReportsConfig(output_dir=rep.get("output_dir", "./.cr-reports"))

    if pv := raw.get("privacy"):
        cfg.privacy = PrivacyConfig(
            ignore=pv.get("ignore", cfg.privacy.ignore),
            redact_patterns=pv.get("redact_patterns", cfg.privacy.redact_patterns),
        )

    return cfg
