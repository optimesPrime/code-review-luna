import pytest
from phases.adapters import get_adapter
from phases.backend_language_adapter import LanguageAdapter


@pytest.mark.parametrize("lang", ["csharp", "java", "python", "nodejs", "go", "php", "cpp"])
def test_get_adapter_returns_language_adapter(lang):
    adapter = get_adapter(lang)
    assert isinstance(adapter, LanguageAdapter)


@pytest.mark.parametrize("alias,expected_name", [
    ("c++", "cpp"),
    ("node.js", "nodejs"),
    ("JAVA", "java"),
    ("Python", "python"),
])
def test_get_adapter_normalizes_language_name(alias, expected_name):
    adapter = get_adapter(alias)
    assert adapter.name == expected_name


def test_get_adapter_raises_for_unknown():
    with pytest.raises(ValueError, match="No adapter registered"):
        get_adapter("rust")


@pytest.mark.parametrize("lang,expected_ext", [
    ("csharp", ".cs"),
    ("java", ".java"),
    ("python", ".py"),
    ("nodejs", ".ts"),
    ("go", ".go"),
    ("php", ".php"),
    ("cpp", ".cpp"),
])
def test_adapter_extensions(lang, expected_ext):
    adapter = get_adapter(lang)
    assert expected_ext in adapter.extensions
