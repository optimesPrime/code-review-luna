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
