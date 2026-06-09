import dataclasses
import json
from pathlib import Path

from terminal_renderer import FixCandidate


def test_fix_candidate_has_required_fields():
    """FixCandidate must carry file/line/evidence/suggestion for luna fix to work."""
    fc = FixCandidate(
        id=1,
        mode="auto",
        title="登录失败后恢复 loading",
        reason="src/views/Login.vue:74",
        command_hint="luna fix 1",
        impact="高价值",
        file="src/views/Login.vue",
        line=74,
        evidence="Login.vue:74 catch 块未恢复 loading",
        suggestion="在 catch 块末尾添加 loading.value = false",
    )
    d = dataclasses.asdict(fc)
    assert d["file"] == "src/views/Login.vue"
    assert d["line"] == 74
    assert d["evidence"] == "Login.vue:74 catch 块未恢复 loading"
    assert d["suggestion"] == "在 catch 块末尾添加 loading.value = false"
