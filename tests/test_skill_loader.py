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
