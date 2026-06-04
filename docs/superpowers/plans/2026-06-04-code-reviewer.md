# Code Reviewer CLI 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 构建 `cr` 命令行工具，对本地 git 改动进行 AI 初审（爆炸范围 + 代码质量），输出 Markdown 审查报告，所有修改建议逐条确认后方可应用。

**Architecture:** CLI 工具读取 git diff，依次经过爆炸范围（Blast Radius）和代码质量两个审查阶段，每阶段独立调用 Anthropic API，结果汇总为带时间戳的 Markdown 报告。默认只输出建议，开启 `--apply` 后才允许逐条写入文件。支持通过 skill .md 文件向各阶段注入自定义审查规范。

**Tech Stack:** Python 3.11+, click 8.x, anthropic SDK 0.28+, pyyaml 6.x, pytest 8.x, pytest-mock 3.x

**Working Directory:** `/Users/wangyinlong/code-reviewer/`

---

## 文件结构

| 文件 | 职责 |
|------|------|
| `cr.py` | CLI 入口，click 命令注册，主流程编排 |
| `config.py` | Config 数据类 + YAML 加载 |
| `config.yaml` | 默认配置模板（用户按需修改） |
| `diff_reader.py` | git diff 读取、大小校验、敏感信息脱敏 |
| `skill_loader.py` | skill .md 文件加载，注入阶段 system prompt |
| `confirmer.py` | 统一 y/N 交互确认 |
| `api_client.py` | Anthropic API 封装，可扩展至 OpenAI |
| `phases/__init__.py` | 空文件，使 phases 成为包 |
| `phases/blast_radius.py` | 阶段1：符号提取、调用关系搜索、API 风险评估 |
| `phases/code_quality.py` | 阶段2：代码质量 API 审查 |
| `test_importer.py` | 测试文件解析、与改动关联映射 |
| `reporter.py` | Markdown 报告渲染与保存 |
| `pyproject.toml` | 包配置，注册 `cr` 命令，pipx 安装入口 |
| `requirements.txt` | 依赖列表 |
| `tests/test_config.py` | 配置加载单元测试 |
| `tests/test_diff_reader.py` | diff 读取和脱敏测试 |
| `tests/test_skill_loader.py` | skill 加载测试 |
| `tests/test_confirmer.py` | 交互确认测试 |
| `tests/test_api_client.py` | API 客户端测试 |
| `tests/test_blast_radius.py` | 符号提取和爆炸范围分析测试 |
| `tests/test_code_quality.py` | 代码质量分析测试 |
| `tests/test_importer.py` | 测试文件解析测试 |
| `tests/test_reporter.py` | 报告渲染测试 |

---

## Task 1: 项目脚手架

**Files:**
- Create: `requirements.txt`
- Create: `pyproject.toml`
- Create: `config.yaml`
- Create: `phases/__init__.py`

- [ ] **Step 1: 确认 Python 版本**

```bash
python3 --version
```

Expected: `Python 3.11.x` 或更高版本

- [ ] **Step 2: 创建 requirements.txt**

```
click>=8.1
anthropic>=0.28
pyyaml>=6.0
pytest>=8.0
pytest-mock>=3.12
```

- [ ] **Step 3: 创建 pyproject.toml**

```toml
[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.backends.legacy:build"

[project]
name = "code-reviewer"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = [
    "click>=8.1",
    "anthropic>=0.28",
    "pyyaml>=6.0",
]

[project.scripts]
cr = "cr:main"

[tool.pytest.ini_options]
testpaths = ["tests"]
```

- [ ] **Step 4: 创建 config.yaml**

```yaml
api:
  provider: anthropic
  model: claude-sonnet-4-6
  api_key_env: ANTHROPIC_API_KEY

review:
  language: zh
  project_type: frontend
  confirm_before_fix: true
  max_diff_chars: 120000
  apply_enabled: false

skills: []

reports:
  output_dir: ./.cr-reports

privacy:
  ignore:
    - .env
    - .env.*
    - node_modules/
    - dist/
  redact_patterns:
    - "Bearer\\s+[A-Za-z0-9._-]+"
    - "AKIA[0-9A-Z]{16}"
```

- [ ] **Step 5: 创建目录和空文件**

```bash
mkdir -p phases tests skills
touch phases/__init__.py
```

- [ ] **Step 6: 安装依赖**

```bash
pip install -r requirements.txt
```

Expected: 所有包安装成功，无报错

- [ ] **Step 7: 初始化 git 并提交**

```bash
git init
printf ".cr-reports/\n__pycache__/\n*.egg-info/\n.env\n" > .gitignore
git add .
git commit -m "chore: project scaffold"
```

---

## Task 2: 配置加载器

**Files:**
- Create: `config.py`
- Create: `tests/test_config.py`

- [ ] **Step 1: 写失败测试**

创建 `tests/test_config.py`：

```python
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
```

- [ ] **Step 2: 运行确认失败**

```bash
pytest tests/test_config.py -v
```

Expected: `ModuleNotFoundError: No module named 'config'`

- [ ] **Step 3: 实现 config.py**

```python
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
```

- [ ] **Step 4: 运行确认通过**

```bash
pytest tests/test_config.py -v
```

Expected: 6 tests PASS

- [ ] **Step 5: 提交**

```bash
git add config.py tests/test_config.py
git commit -m "feat: config loader with YAML support and env var API key"
```

---

## Task 3: Diff 读取器 + 脱敏

**Files:**
- Create: `diff_reader.py`
- Create: `tests/test_diff_reader.py`

- [ ] **Step 1: 写失败测试**

创建 `tests/test_diff_reader.py`：

```python
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
```

- [ ] **Step 2: 运行确认失败**

```bash
pytest tests/test_diff_reader.py -v
```

Expected: `ModuleNotFoundError: No module named 'diff_reader'`

- [ ] **Step 3: 实现 diff_reader.py**

```python
from __future__ import annotations
import re
import subprocess
from typing import Optional


class DiffError(Exception):
    pass


def get_diff(staged: bool = False, since: Optional[str] = None) -> str:
    check = subprocess.run(
        ["git", "rev-parse", "--git-dir"],
        capture_output=True, text=True,
    )
    if check.returncode != 0:
        raise DiffError("当前目录不是 git 仓库，请在项目根目录下运行 cr")

    if staged:
        cmd = ["git", "diff", "--cached"]
    elif since:
        cmd = ["git", "diff", since]
    else:
        cmd = ["git", "diff", "HEAD"]

    result = subprocess.run(cmd, capture_output=True, text=True)
    return result.stdout


def redact(text: str, patterns: list[str]) -> str:
    for pattern in patterns:
        text = re.sub(pattern, "[REDACTED]", text)
    return text
```

- [ ] **Step 4: 运行确认通过**

```bash
pytest tests/test_diff_reader.py -v
```

Expected: 6 tests PASS

- [ ] **Step 5: 提交**

```bash
git add diff_reader.py tests/test_diff_reader.py
git commit -m "feat: diff reader with sensitive data redaction"
```

---

## Task 4: Skill 加载器

**Files:**
- Create: `skill_loader.py`
- Create: `tests/test_skill_loader.py`

- [ ] **Step 1: 写失败测试**

创建 `tests/test_skill_loader.py`：

```python
from config import SkillEntry
from skill_loader import load_skills, SkillLoadError


def test_loads_skill_file(tmp_path):
    f = tmp_path / "vue.md"
    f.write_text("# Vue 规范\n总是使用 Composition API")
    context, errors = load_skills([SkillEntry("vue", str(f))])
    assert "Vue 规范" in context
    assert len(errors) == 0


def test_missing_file_returns_error():
    context, errors = load_skills([SkillEntry("missing", "/nonexistent/file.md")])
    assert context == ""
    assert len(errors) == 1
    assert errors[0].name == "missing"
    assert "不存在" in errors[0].reason


def test_empty_file_returns_error(tmp_path):
    f = tmp_path / "empty.md"
    f.write_text("")
    context, errors = load_skills([SkillEntry("empty", str(f))])
    assert context == ""
    assert len(errors) == 1
    assert "为空" in errors[0].reason


def test_multiple_skills_combined(tmp_path):
    f1 = tmp_path / "a.md"
    f1.write_text("Skill A content")
    f2 = tmp_path / "b.md"
    f2.write_text("Skill B content")
    context, errors = load_skills([SkillEntry("a", str(f1)), SkillEntry("b", str(f2))])
    assert "Skill A content" in context
    assert "Skill B content" in context
    assert len(errors) == 0


def test_partial_failure(tmp_path):
    good = tmp_path / "good.md"
    good.write_text("Good content")
    context, errors = load_skills([SkillEntry("good", str(good)), SkillEntry("bad", "/missing.md")])
    assert "Good content" in context
    assert len(errors) == 1
```

- [ ] **Step 2: 运行确认失败**

```bash
pytest tests/test_skill_loader.py -v
```

Expected: `ModuleNotFoundError: No module named 'skill_loader'`

- [ ] **Step 3: 实现 skill_loader.py**

```python
from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
from config import SkillEntry


@dataclass
class SkillLoadError:
    name: str
    reason: str


def load_skills(entries: list[SkillEntry]) -> tuple[str, list[SkillLoadError]]:
    parts: list[str] = []
    errors: list[SkillLoadError] = []

    for entry in entries:
        p = Path(entry.path)
        if not p.exists():
            errors.append(SkillLoadError(entry.name, f"文件不存在: {entry.path}"))
            continue
        content = p.read_text(encoding="utf-8").strip()
        if not content:
            errors.append(SkillLoadError(entry.name, f"文件为空: {entry.path}"))
            continue
        parts.append(f"# Skill: {entry.name}\n{content}")

    return "\n\n".join(parts), errors
```

- [ ] **Step 4: 运行确认通过**

```bash
pytest tests/test_skill_loader.py -v
```

Expected: 5 tests PASS

- [ ] **Step 5: 提交**

```bash
git add skill_loader.py tests/test_skill_loader.py
git commit -m "feat: skill loader with error recovery for missing/empty files"
```

---

## Task 5: 交互确认模块

**Files:**
- Create: `confirmer.py`
- Create: `tests/test_confirmer.py`

- [ ] **Step 1: 写失败测试**

创建 `tests/test_confirmer.py`：

```python
from unittest.mock import patch
from confirmer import ask


def test_yes_answer():
    with patch("builtins.input", return_value="y"):
        assert ask("继续?") is True


def test_no_answer():
    with patch("builtins.input", return_value="n"):
        assert ask("继续?") is False


def test_empty_defaults_to_false():
    with patch("builtins.input", return_value=""):
        assert ask("继续?") is False


def test_empty_defaults_to_true_when_set():
    with patch("builtins.input", return_value=""):
        assert ask("继续?", default=True) is True


def test_yes_full_word():
    with patch("builtins.input", return_value="yes"):
        assert ask("继续?") is True


def test_eof_returns_false():
    with patch("builtins.input", side_effect=EOFError):
        assert ask("继续?") is False


def test_keyboard_interrupt_returns_false():
    with patch("builtins.input", side_effect=KeyboardInterrupt):
        assert ask("继续?") is False
```

- [ ] **Step 2: 运行确认失败**

```bash
pytest tests/test_confirmer.py -v
```

Expected: `ModuleNotFoundError: No module named 'confirmer'`

- [ ] **Step 3: 实现 confirmer.py**

```python
def ask(prompt: str, default: bool = False) -> bool:
    hint = "[Y/n]" if default else "[y/N]"
    try:
        answer = input(f"{prompt} {hint} ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        return False

    if answer == "":
        return default
    return answer in ("y", "yes")
```

- [ ] **Step 4: 运行确认通过**

```bash
pytest tests/test_confirmer.py -v
```

Expected: 7 tests PASS

- [ ] **Step 5: 提交**

```bash
git add confirmer.py tests/test_confirmer.py
git commit -m "feat: confirmer with y/N interactive prompts and EOF/interrupt safety"
```

---

## Task 6: API 客户端封装

**Files:**
- Create: `api_client.py`
- Create: `tests/test_api_client.py`

- [ ] **Step 1: 写失败测试**

创建 `tests/test_api_client.py`：

```python
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
```

- [ ] **Step 2: 运行确认失败**

```bash
pytest tests/test_api_client.py -v
```

Expected: `ModuleNotFoundError: No module named 'api_client'`

- [ ] **Step 3: 实现 api_client.py**

```python
from anthropic import Anthropic
from config import Config


def call_claude(system_prompt: str, user_prompt: str, config: Config) -> str:
    client = Anthropic(api_key=config.api.api_key)
    response = client.messages.create(
        model=config.api.model,
        max_tokens=4096,
        system=system_prompt,
        messages=[{"role": "user", "content": user_prompt}],
    )
    return response.content[0].text
```

- [ ] **Step 4: 运行确认通过**

```bash
pytest tests/test_api_client.py -v
```

Expected: 2 tests PASS

- [ ] **Step 5: 提交**

```bash
git add api_client.py tests/test_api_client.py
git commit -m "feat: Anthropic API client wrapper"
```

---

## Task 7: 爆炸范围分析器

**Files:**
- Create: `phases/blast_radius.py`
- Create: `tests/test_blast_radius.py`

- [ ] **Step 1: 写失败测试**

创建 `tests/test_blast_radius.py`：

```python
import json
from unittest.mock import patch
from config import Config
from phases.blast_radius import extract_changed_symbols, analyze, BlastRadiusItem


SAMPLE_DIFF = """\
diff --git a/src/composables/useAuth.js b/src/composables/useAuth.js
index 1234567..abcdefg 100644
--- a/src/composables/useAuth.js
+++ b/src/composables/useAuth.js
@@ -1,5 +1,10 @@
+export function refreshToken(token) {
+  return fetch('/api/refresh', { method: 'POST', body: token })
+}
+
+export const clearSession = () => {
+  localStorage.removeItem('token')
+}
"""


def test_extract_changed_symbols():
    symbols = extract_changed_symbols(SAMPLE_DIFF)
    assert "refreshToken" in symbols
    assert "clearSession" in symbols


def test_extract_no_symbols_when_only_deletions():
    diff = "diff --git a/foo.js b/foo.js\n-removed line\n"
    symbols = extract_changed_symbols(diff)
    assert symbols == []


def test_analyze_returns_blast_radius_items(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    cfg = Config()
    api_response = json.dumps([{
        "file": "router/index.js",
        "line": 45,
        "symbol": "refreshToken",
        "risk": "high",
        "confidence": "high",
        "reason": "路由守卫依赖此函数",
        "suggestion": "增加 token 有效性校验",
        "needs_human_review": False,
    }])
    with patch("phases.blast_radius.call_claude", return_value=api_response), \
         patch("phases.blast_radius.find_usages_in_project", return_value=""):
        items = analyze(SAMPLE_DIFF, "", cfg)
    assert len(items) == 1
    assert items[0].file == "router/index.js"
    assert items[0].risk == "high"
    assert items[0].symbol == "refreshToken"


def test_analyze_returns_empty_on_invalid_json(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    cfg = Config()
    with patch("phases.blast_radius.call_claude", return_value="无法分析"), \
         patch("phases.blast_radius.find_usages_in_project", return_value=""):
        items = analyze(SAMPLE_DIFF, "", cfg)
    assert items == []
```

- [ ] **Step 2: 运行确认失败**

```bash
pytest tests/test_blast_radius.py -v
```

Expected: `ModuleNotFoundError: No module named 'phases.blast_radius'`

- [ ] **Step 3: 实现 phases/blast_radius.py**

```python
from __future__ import annotations
import json
import re
import subprocess
from dataclasses import dataclass
from typing import Optional

from api_client import call_claude
from config import Config


@dataclass
class BlastRadiusItem:
    file: str
    line: int
    symbol: str
    risk: str
    confidence: str
    reason: str
    suggestion: Optional[str] = None
    needs_human_review: bool = False


_SYSTEM_PROMPT = """\
你是资深代码审查工程师，专注于分析代码改动的爆炸范围（Blast Radius）。

任务：
1. 分析 git diff 中的改动点（函数、组件、接口、导出等）
2. 结合调用关系，评估每处受影响位置的风险
3. 高风险低置信度的项目标注 needs_human_review=true

{skill_context}

以 JSON 数组输出，每个元素包含：
- file: 受影响文件路径（字符串）
- line: 行号（整数）
- symbol: 改动的符号名（字符串）
- risk: "high" | "medium" | "low"
- confidence: "high" | "medium" | "low"
- reason: 影响原因（中文）
- suggestion: 修复建议，无则 null
- needs_human_review: bool

只输出 JSON 数组，不要其他内容。"""


def extract_changed_symbols(diff: str) -> list[str]:
    symbols: set[str] = set()
    patterns = [
        r"^\+[^+].*?(?:async\s+)?function\s+(\w+)",
        r"^\+[^+].*?(?:const|let|var)\s+(\w+)\s*=\s*(?:async\s+)?\(",
        r"^\+[^+].*?(?:const|let|var)\s+(\w+)\s*=\s*(?:async\s+)?function",
        r"^\+[^+].*?export\s+(?:const|let)\s+(\w+)",
        r"^\+[^+].*?class\s+(\w+)",
    ]
    for line in diff.split("\n"):
        for pat in patterns:
            m = re.search(pat, line)
            if m:
                symbols.add(m.group(1))
    return list(symbols)


def find_usages_in_project(symbols: list[str], ignore_dirs: list[str]) -> str:
    if not symbols:
        return ""
    results: list[str] = []
    for symbol in symbols[:10]:
        cmd = [
            "grep", "-rn",
            "--include=*.vue", "--include=*.ts",
            "--include=*.js", "--include=*.tsx",
            symbol, ".",
        ]
        for d in ignore_dirs:
            cmd.extend(["--exclude-dir", d.rstrip("/")])
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
            if result.stdout.strip():
                results.append(f"# `{symbol}` 的使用位置:\n{result.stdout[:3000]}")
        except subprocess.TimeoutExpired:
            results.append(f"# `{symbol}`: 搜索超时")
    return "\n".join(results)


def analyze(diff: str, skill_context: str, config: Config) -> list[BlastRadiusItem]:
    symbols = extract_changed_symbols(diff)
    usages = find_usages_in_project(symbols, config.privacy.ignore)

    system = _SYSTEM_PROMPT.format(skill_context=skill_context or "")
    user = (
        f"## Git Diff\n\n```diff\n{diff}\n```\n\n"
        f"## 调用关系\n\n{usages or '（未找到外部调用）'}"
    )

    raw = call_claude(system, user, config)

    match = re.search(r"\[.*\]", raw, re.DOTALL)
    if not match:
        return []

    try:
        items_raw = json.loads(match.group())
    except json.JSONDecodeError:
        return []

    return [
        BlastRadiusItem(
            file=item.get("file", ""),
            line=int(item.get("line", 0)),
            symbol=item.get("symbol", ""),
            risk=item.get("risk", "low"),
            confidence=item.get("confidence", "medium"),
            reason=item.get("reason", ""),
            suggestion=item.get("suggestion"),
            needs_human_review=bool(item.get("needs_human_review", False)),
        )
        for item in items_raw
        if isinstance(item, dict)
    ]
```

- [ ] **Step 4: 运行确认通过**

```bash
pytest tests/test_blast_radius.py -v
```

Expected: 4 tests PASS

- [ ] **Step 5: 提交**

```bash
git add phases/blast_radius.py tests/test_blast_radius.py
git commit -m "feat: blast radius analyzer with symbol extraction and API risk assessment"
```

---

## Task 8: 代码质量分析器

**Files:**
- Create: `phases/code_quality.py`
- Create: `tests/test_code_quality.py`

- [ ] **Step 1: 写失败测试**

创建 `tests/test_code_quality.py`：

```python
import json
from unittest.mock import patch
from config import Config
from phases.code_quality import analyze, CodeQualityItem


SAMPLE_DIFF = """\
diff --git a/src/views/Login.vue b/src/views/Login.vue
--- a/src/views/Login.vue
+++ b/src/views/Login.vue
@@ -10,6 +10,12 @@
+const handleSubmit = async () => {
+  localStorage.removeItem('token')
+  const res = await login(form)
+  if (res.code === 200) {
+    localStorage.removeItem('token')
+    router.push('/')
+  }
+}
"""


def test_analyze_returns_quality_items(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    cfg = Config()
    api_response = json.dumps([{
        "file": "src/views/Login.vue",
        "line": 14,
        "issue_type": "redundant",
        "description": "重复的 token 清理逻辑",
        "evidence": "第 11 行与第 14 行均调用 localStorage.removeItem('token')",
        "risk": "low",
        "confidence": "high",
        "suggestion": "删除第 14 行重复调用",
    }])
    with patch("phases.code_quality.call_claude", return_value=api_response):
        items = analyze(SAMPLE_DIFF, "", cfg)
    assert len(items) == 1
    assert items[0].issue_type == "redundant"
    assert items[0].risk == "low"


def test_analyze_returns_empty_on_no_issues(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    cfg = Config()
    with patch("phases.code_quality.call_claude", return_value="[]"):
        items = analyze(SAMPLE_DIFF, "", cfg)
    assert items == []


def test_analyze_returns_empty_on_invalid_json(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    cfg = Config()
    with patch("phases.code_quality.call_claude", return_value="无问题"):
        items = analyze(SAMPLE_DIFF, "", cfg)
    assert items == []
```

- [ ] **Step 2: 运行确认失败**

```bash
pytest tests/test_code_quality.py -v
```

Expected: `ModuleNotFoundError: No module named 'phases.code_quality'`

- [ ] **Step 3: 实现 phases/code_quality.py**

```python
from __future__ import annotations
import json
import re
from dataclasses import dataclass
from typing import Optional

from api_client import call_claude
from config import Config


@dataclass
class CodeQualityItem:
    file: str
    line: int
    issue_type: str
    description: str
    evidence: str
    risk: str
    confidence: str
    suggestion: Optional[str] = None


_SYSTEM_PROMPT = """\
你是代码质量审查工程师，分析改动代码本身的质量问题。

检查项：
1. 冗余逻辑（重复代码、死代码）
2. 多余判断（永真/永假条件）
3. 关键路径异常处理是否完整
4. 整体流程是否能走通

{skill_context}

以 JSON 数组输出，每个元素包含：
- file: 文件路径（字符串）
- line: 行号（整数）
- issue_type: "redundant" | "dead_code" | "missing_error_handling" | "logic_gap"
- description: 问题描述（中文）
- evidence: 判断依据（引用具体代码）
- risk: "high" | "medium" | "low"
- confidence: "high" | "medium" | "low"
- suggestion: 修复建议，无则 null

只输出 JSON 数组，不要其他内容。"""


def analyze(diff: str, skill_context: str, config: Config) -> list[CodeQualityItem]:
    system = _SYSTEM_PROMPT.format(skill_context=skill_context or "")
    user = f"## Git Diff\n\n```diff\n{diff}\n```"

    raw = call_claude(system, user, config)

    match = re.search(r"\[.*\]", raw, re.DOTALL)
    if not match:
        return []

    try:
        items_raw = json.loads(match.group())
    except json.JSONDecodeError:
        return []

    return [
        CodeQualityItem(
            file=item.get("file", ""),
            line=int(item.get("line", 0)),
            issue_type=item.get("issue_type", "logic_gap"),
            description=item.get("description", ""),
            evidence=item.get("evidence", ""),
            risk=item.get("risk", "low"),
            confidence=item.get("confidence", "medium"),
            suggestion=item.get("suggestion"),
        )
        for item in items_raw
        if isinstance(item, dict)
    ]
```

- [ ] **Step 4: 运行确认通过**

```bash
pytest tests/test_code_quality.py -v
```

Expected: 3 tests PASS

- [ ] **Step 5: 提交**

```bash
git add phases/code_quality.py tests/test_code_quality.py
git commit -m "feat: code quality analyzer for redundancy, dead code, and logic gaps"
```

---

## Task 9: 测试用例导入器

**Files:**
- Create: `test_importer.py`
- Create: `tests/test_importer.py`

- [ ] **Step 1: 写失败测试**

创建 `tests/test_importer.py`：

```python
from pathlib import Path
from test_importer import parse_test_file, find_related_tests


SAMPLE_SPEC = """\
import { describe, it, expect } from 'vitest'

describe('useAuth', () => {
  it('should refresh token on expiry', () => {
    expect(true).toBe(true)
  })

  it('should clear session on logout', () => {
    expect(true).toBe(true)
  })
})

describe('Login page', () => {
  it('renders login form', () => {})
})
"""

SAMPLE_DIFF = "diff --git a/src/composables/useAuth.js b/src/composables/useAuth.js\n+++ b/src/composables/useAuth.js\n"


def test_parses_test_cases(tmp_path):
    f = tmp_path / "useAuth.spec.ts"
    f.write_text(SAMPLE_SPEC)
    cases = parse_test_file(str(f))
    assert len(cases) == 3
    its = [c.it for c in cases]
    assert "should refresh token on expiry" in its
    assert "should clear session on logout" in its


def test_parse_empty_file(tmp_path):
    f = tmp_path / "empty.spec.ts"
    f.write_text("")
    assert parse_test_file(str(f)) == []


def test_parse_nonexistent_file():
    assert parse_test_file("/nonexistent/file.spec.ts") == []


def test_find_related_tests(tmp_path):
    f = tmp_path / "useAuth.spec.ts"
    f.write_text(SAMPLE_SPEC)
    all_cases = parse_test_file(str(f))
    related = find_related_tests(all_cases, SAMPLE_DIFF)
    assert len(related) > 0
    assert all("useauth" in c.describe.lower() or "useauth" in c.it.lower() for c in related)


def test_find_related_no_match(tmp_path):
    f = tmp_path / "unrelated.spec.ts"
    f.write_text("describe('Other', () => { it('does nothing', () => {}) })")
    cases = parse_test_file(str(f))
    related = find_related_tests(cases, SAMPLE_DIFF)
    assert related == []
```

- [ ] **Step 2: 运行确认失败**

```bash
pytest tests/test_importer.py -v
```

Expected: `ModuleNotFoundError: No module named 'test_importer'`

- [ ] **Step 3: 实现 test_importer.py**

```python
from __future__ import annotations
import re
from dataclasses import dataclass
from pathlib import Path


@dataclass
class TestCase:
    file: str
    describe: str
    it: str
    line: int


def parse_test_file(file_path: str) -> list[TestCase]:
    p = Path(file_path)
    if not p.exists():
        return []

    lines = p.read_text(encoding="utf-8").split("\n")
    results: list[TestCase] = []
    current_describe = ""

    for i, line in enumerate(lines, 1):
        dm = re.search(r"describe\s*\(\s*['\"`](.+?)['\"`]", line)
        if dm:
            current_describe = dm.group(1)
            continue
        im = re.search(r"(?:it|test)\s*\(\s*['\"`](.+?)['\"`]", line)
        if im:
            results.append(TestCase(
                file=file_path,
                describe=current_describe,
                it=im.group(1),
                line=i,
            ))

    return results


def find_related_tests(test_cases: list[TestCase], diff: str) -> list[TestCase]:
    changed_stems: set[str] = set()
    for line in diff.split("\n"):
        if line.startswith("+++ b/"):
            stem = Path(line[6:]).stem.lower()
            stem = re.sub(r"\.(spec|test)$", "", stem)
            changed_stems.add(stem)

    related: list[TestCase] = []
    for tc in test_cases:
        for stem in changed_stems:
            if stem in tc.describe.lower() or stem in tc.it.lower():
                related.append(tc)
                break

    return related
```

- [ ] **Step 4: 运行确认通过**

```bash
pytest tests/test_importer.py -v
```

Expected: 5 tests PASS

- [ ] **Step 5: 提交**

```bash
git add test_importer.py tests/test_importer.py
git commit -m "feat: test case importer with describe/it parsing and diff-based matching"
```

---

## Task 10: 报告生成器

**Files:**
- Create: `reporter.py`
- Create: `tests/test_reporter.py`

- [ ] **Step 1: 写失败测试**

创建 `tests/test_reporter.py`：

```python
from pathlib import Path
from phases.blast_radius import BlastRadiusItem
from phases.code_quality import CodeQualityItem
from test_importer import TestCase
from reporter import ReviewReport, render, save


def make_report(**kwargs) -> ReviewReport:
    defaults = dict(timestamp="2026-06-04 10:00", diff_summary="共 20 行改动")
    defaults.update(kwargs)
    return ReviewReport(**defaults)


def test_render_contains_timestamp():
    assert "2026-06-04 10:00" in render(make_report())


def test_render_with_blast_radius_items():
    items = [BlastRadiusItem(
        file="router/index.js", line=45, symbol="refreshToken",
        risk="high", confidence="high", reason="路由守卫依赖此函数",
        suggestion="增加校验",
    )]
    md = render(make_report(blast_radius_items=items))
    assert "router/index.js" in md
    assert "路由守卫依赖此函数" in md


def test_render_no_blast_radius_items():
    assert "未发现爆炸范围影响" in render(make_report())


def test_render_with_code_quality_items():
    items = [CodeQualityItem(
        file="Login.vue", line=14, issue_type="redundant",
        description="重复的 token 清理", evidence="第11行和第14行",
        risk="low", confidence="high", suggestion="删除第14行",
    )]
    md = render(make_report(code_quality_items=items))
    assert "Login.vue" in md
    assert "重复的 token 清理" in md


def test_render_with_related_tests():
    tests = [TestCase(file="tests/useAuth.spec.ts", describe="useAuth", it="refreshes token", line=5)]
    md = render(make_report(related_tests=tests))
    assert "useAuth.spec.ts" in md


def test_save_writes_file(tmp_path):
    path = save(make_report(), str(tmp_path))
    assert Path(path).exists()
    assert "2026-06-04 10:00" in Path(path).read_text()


def test_save_creates_nested_dir(tmp_path):
    path = save(make_report(), str(tmp_path / "nested" / "reports"))
    assert Path(path).exists()
```

- [ ] **Step 2: 运行确认失败**

```bash
pytest tests/test_reporter.py -v
```

Expected: `ModuleNotFoundError: No module named 'reporter'`

- [ ] **Step 3: 实现 reporter.py**

```python
from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import Path
from phases.blast_radius import BlastRadiusItem
from phases.code_quality import CodeQualityItem
from test_importer import TestCase


@dataclass
class ReviewReport:
    timestamp: str
    diff_summary: str
    blast_radius_items: list[BlastRadiusItem] = field(default_factory=list)
    code_quality_items: list[CodeQualityItem] = field(default_factory=list)
    related_tests: list[TestCase] = field(default_factory=list)
    skill_errors: list = field(default_factory=list)
    applied_fixes: list[str] = field(default_factory=list)
    skipped_items: list[str] = field(default_factory=list)


def _blast_section(items: list[BlastRadiusItem]) -> str:
    if not items:
        return "_未发现爆炸范围影响_"
    risk_order = {"high": 0, "medium": 1, "low": 2}
    lines = []
    for item in sorted(items, key=lambda x: risk_order.get(x.risk, 9)):
        note = " *(需人工确认)*" if item.needs_human_review else ""
        lines.append(
            f"### `{item.symbol}` → `{item.file}:{item.line}`\n"
            f"- 风险: **{item.risk}** | 置信度: {item.confidence}{note}\n"
            f"- 原因: {item.reason}\n"
            + (f"- 建议: {item.suggestion}\n" if item.suggestion else "")
        )
    return "\n".join(lines)


def _quality_section(items: list[CodeQualityItem]) -> str:
    if not items:
        return "_未发现代码质量问题_"
    lines = []
    for item in items:
        lines.append(
            f"### `{item.file}:{item.line}` — {item.description}\n"
            f"- 类型: {item.issue_type} | 风险: **{item.risk}** | 置信度: {item.confidence}\n"
            f"- 依据: {item.evidence}\n"
            + (f"- 建议: {item.suggestion}\n" if item.suggestion else "")
        )
    return "\n".join(lines)


def _tests_section(tests: list[TestCase]) -> str:
    if not tests:
        return "_未导入测试用例或无关联用例_"
    return "\n".join(
        f"- `{tc.file}:{tc.line}` — {tc.describe} > {tc.it}"
        for tc in tests
    )


def render(report: ReviewReport) -> str:
    return f"""# 代码审查报告 · {report.timestamp}

## 一、改动概述

{report.diff_summary}

## 二、爆炸范围分析

{_blast_section(report.blast_radius_items)}

## 三、代码质量问题

{_quality_section(report.code_quality_items)}

## 四、关联测试用例

{_tests_section(report.related_tests)}

## 五、审查结论

> 由人工复审填写

---
*cr tool · 仅供参考*
"""


def save(report: ReviewReport, output_dir: str) -> str:
    d = Path(output_dir)
    d.mkdir(parents=True, exist_ok=True)
    safe_ts = report.timestamp.replace(":", "").replace(" ", "_")
    path = d / f"{safe_ts}_report.md"
    path.write_text(render(report), encoding="utf-8")
    return str(path)
```

- [ ] **Step 4: 运行确认通过**

```bash
pytest tests/test_reporter.py -v
```

Expected: 7 tests PASS

- [ ] **Step 5: 全量测试（阶段检查点）**

```bash
pytest tests/ -v
```

Expected: 40 tests PASS across all modules

- [ ] **Step 6: 提交**

```bash
git add reporter.py tests/test_reporter.py
git commit -m "feat: markdown report renderer with blast radius and code quality sections"
```

---

## Task 11: CLI 入口 + 安装

**Files:**
- Create: `cr.py`

- [ ] **Step 1: 创建 cr.py**

```python
from __future__ import annotations
import json
import sys
from datetime import datetime
from pathlib import Path

import click

from config import load_config
from diff_reader import get_diff, redact, DiffError
from skill_loader import load_skills
from confirmer import ask
import phases.blast_radius as blast
import phases.code_quality as quality
from test_importer import parse_test_file, find_related_tests
from reporter import ReviewReport, save, render


@click.group()
def cli():
    pass


@cli.command()
@click.option("--staged", is_flag=True, help="只审查已 git add 的内容")
@click.option("--since", default=None, help="审查相对某个 ref 的改动，如 main")
@click.option("--tests", default=None, help="测试文件或目录路径")
@click.option("--phase", default=None, type=click.Choice(["blast", "quality"]))
@click.option("--apply", "apply_mode", is_flag=True, help="开启可写入模式，仍需逐条确认")
@click.option("--output", default=None, help="自定义报告输出路径")
@click.option("--format", "fmt", default="markdown",
              type=click.Choice(["markdown", "json"]))
@click.option("--config", "config_path", default="config.yaml")
def run(staged, since, tests, phase, apply_mode, output, fmt, config_path):
    """对当前 git 改动执行 AI 代码审查"""
    cfg = load_config(config_path)
    if apply_mode:
        cfg.review.apply_enabled = True

    try:
        diff = get_diff(staged=staged, since=since)
    except DiffError as e:
        click.echo(f"错误: {e}", err=True)
        sys.exit(1)

    if not diff.strip():
        click.echo("无改动，退出审查。")
        return

    if len(diff) > cfg.review.max_diff_chars:
        click.echo(
            f"diff 过大（{len(diff)} 字符），超过限制 {cfg.review.max_diff_chars}。\n"
            "建议使用 --staged 或 --since 缩小范围。",
            err=True,
        )
        sys.exit(1)

    diff = redact(diff, cfg.privacy.redact_patterns)

    skill_context, skill_errors = load_skills(cfg.skills)
    for err in skill_errors:
        click.echo(f"[Skill 加载失败] {err.name}: {err.reason}", err=True)

    test_cases = []
    if tests:
        tp = Path(tests)
        if tp.is_file():
            test_cases = parse_test_file(str(tp))
        elif tp.is_dir():
            for f in list(tp.rglob("*.spec.*")) + list(tp.rglob("*.test.*")):
                test_cases.extend(parse_test_file(str(f)))

    related_tests = find_related_tests(test_cases, diff)

    report = ReviewReport(
        timestamp=datetime.now().strftime("%Y-%m-%d %H:%M"),
        diff_summary=f"共 {diff.count(chr(10))} 行改动",
        skill_errors=skill_errors,
        related_tests=related_tests,
    )

    if phase in (None, "blast"):
        click.echo("\n[阶段1] 爆炸范围分析中...\n")
        blast_items = blast.analyze(diff, skill_context, cfg)
        report.blast_radius_items = blast_items

        for item in sorted(blast_items, key=lambda x: {"high": 0, "medium": 1, "low": 2}[x.risk]):
            note = " [需人工确认]" if item.needs_human_review else ""
            click.echo(f"[爆炸范围·{item.risk}] {item.file}:{item.line} — {item.reason}{note}")
            if item.suggestion and ask("  查看修复建议？"):
                click.echo(f"  建议: {item.suggestion}")
                if cfg.review.apply_enabled:
                    if ask("  应用此修改？"):
                        report.applied_fixes.append(f"blast:{item.file}:{item.line}")
                        click.echo("  [已记录，请按建议手动应用]")
                    else:
                        report.skipped_items.append(f"blast:{item.file}:{item.line}")
                else:
                    if ask("  生成 patch 供人工复制？"):
                        click.echo(f"\n--- patch ---\n{item.suggestion}\n--- end ---\n")

    if phase in (None, "quality"):
        click.echo("\n[阶段2] 代码质量审查中...\n")
        quality_items = quality.analyze(diff, skill_context, cfg)
        report.code_quality_items = quality_items

        for item in quality_items:
            click.echo(f"[代码质量·{item.risk}] {item.file}:{item.line} — {item.description}")
            click.echo(f"  依据: {item.evidence}")
            if item.suggestion and ask("  查看修复建议？"):
                click.echo(f"  建议: {item.suggestion}")
                if cfg.review.apply_enabled:
                    if ask("  应用此修改？"):
                        report.applied_fixes.append(f"quality:{item.file}:{item.line}")
                    else:
                        report.skipped_items.append(f"quality:{item.file}:{item.line}")

    if fmt == "json":
        click.echo(json.dumps({
            "blast_radius": [vars(i) for i in report.blast_radius_items],
            "code_quality": [vars(i) for i in report.code_quality_items],
        }, ensure_ascii=False, indent=2))
        return

    out_dir = output or cfg.reports.output_dir
    path = save(report, out_dir)
    click.echo(f"\n报告已保存：{path}")

    high_count = sum(1 for i in report.blast_radius_items if i.risk == "high")
    if high_count:
        click.echo(f"注意：发现 {high_count} 处高风险爆炸范围，请重点复审。")


def main():
    cli()


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: 全量测试**

```bash
pytest tests/ -v
```

Expected: All 40 tests PASS

- [ ] **Step 3: 配置 API Key 并试跑**

```bash
export ANTHROPIC_API_KEY="your-api-key-here"

# 在任意有 git 改动的项目目录中
cd /Users/wangyinlong/fund-live-project
python /Users/wangyinlong/code-reviewer/cr.py run
```

Expected: 输出爆炸范围分析和代码质量审查结果，在当前目录生成 `.cr-reports/*.md`

- [ ] **Step 4: 用 pipx 安装为全局命令**

```bash
cd /Users/wangyinlong/code-reviewer
pipx install .
```

Expected:
```
installed package code-reviewer 0.1.0
These apps are now globally available: cr
```

- [ ] **Step 5: 验证全局命令可用**

```bash
cd /Users/wangyinlong/fund-live-project
cr run --staged
```

Expected: 工具正常运行，输出审查结果

- [ ] **Step 6: 提交**

```bash
cd /Users/wangyinlong/code-reviewer
git add cr.py
git commit -m "feat: CLI entry with full phase orchestration and pipx install support"
```

---

## 自检：Spec 覆盖确认

| 设计要求 | 对应 Task |
|---------|---------|
| git diff 读取 + 脱敏 | Task 3 |
| Skill 文件加载 + 注入 | Task 4 |
| 爆炸范围分析（Blast Radius） | Task 7 |
| 代码质量审查 | Task 8 |
| 测试用例导入 + 关联映射 | Task 9 |
| 逐条交互确认，禁止私自修改 | Task 5, Task 11 |
| `--apply` 显式开启可写入 | Task 2 (config), Task 11 |
| `--since main` 支持 | Task 3, Task 11 |
| `--format json` 输出 | Task 11 |
| diff 大小限制 + 提示 | Task 11 |
| Markdown 报告带时间戳 | Task 10 |
| pipx 全局安装为 `cr` 命令 | Task 1, Task 11 |
| 失败路径降级（API 失败、skill 缺失等） | Task 4, Task 7, Task 8 |
