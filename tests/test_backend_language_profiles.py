# tests/test_backend_language_profiles.py
from phases.backend_language_profiles import get_profile, supported_languages


def test_supported_languages_include_common_backend_languages():
    assert set(supported_languages()) == {"csharp", "java", "python", "nodejs", "go", "php", "cpp"}


def test_csharp_profile_has_controller_and_dto_nodes():
    profile = get_profile("csharp")
    assert ".cs" in profile.extensions
    assert "controller_action" in profile.node_types
    assert "model_property" in profile.node_types
    assert "Roslyn" in profile.future_parser


def test_java_profile_mentions_spring_risk_signals():
    profile = get_profile("java")
    assert ".java" in profile.extensions
    assert "spring_controller" in profile.entrypoint_types
    assert "@Transactional" in profile.high_risk_signals


def test_python_profile_mentions_fastapi_and_django():
    profile = get_profile("python")
    assert ".py" in profile.extensions
    assert "fastapi_route" in profile.entrypoint_types
    assert "django_view" in profile.entrypoint_types


def test_cpp_profile_mentions_memory_and_concurrency():
    profile = get_profile("cpp")
    assert ".cpp" in profile.extensions
    assert "pointer_lifetime_changed" in profile.high_risk_signals
    assert "lock_or_thread_boundary_changed" in profile.high_risk_signals
