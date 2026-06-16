import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import dataclasses
import json
from reporter import ReviewReport
from phases.blast_radius import BlastRadiusItem
from phases.code_quality import CodeQualityItem
from web_reporter import to_web_data


def _make_report():
    r = ReviewReport(timestamp="2026-06-16 14:32", diff_summary="test diff")
    r.blast_radius_items = [
        BlastRadiusItem(file="pages/a.vue", line=10, symbol="fn", risk="high",
                        confidence="high", reason="高风险原因", suggestion="修复建议")
    ]
    r.code_quality_items = [
        CodeQualityItem(file="src/b.ts", line=5, issue_type="missing_error_handling",
                        risk="medium", confidence="medium",
                        description="缺少错误处理", evidence="L5: fetch()", suggestion="加 try/catch")
    ]
    return r


def test_to_web_data_meta():
    data = to_web_data(_make_report(), branch="feat/test", server_port=9001)
    assert data["meta"]["branch"] == "feat/test"
    assert data["meta"]["server_port"] == 9001
    assert data["meta"]["timestamp"] == "2026-06-16 14:32"
    assert data["meta"]["verdict"] in ("pass", "needs_review", "critical")


def test_to_web_data_issues_merged():
    data = to_web_data(_make_report(), branch="feat/test", server_port=9001)
    assert len(data["issues"]) == 2
    high = next(i for i in data["issues"] if i["risk"] == "high")
    assert high["file"] == "pages/a.vue"
    assert high["line"] == 10
    assert high["title"] == "高风险原因"
    assert high["suggestion"] == "修复建议"
    assert "id" in high
    assert high["mode"] in ("auto", "assist", "manual")


from web_reporter import build_blast_clusters


def test_build_blast_clusters_groups_by_source():
    r = ReviewReport(timestamp="2026-06-16 14:32", diff_summary="")
    r.changed_symbols = [{"file": "src/index.vue", "name": "myFn", "symbol": "myFn"}]
    r.impact_paths = [
        {"path": ["src/index.vue", "src/page.vue"], "risk": "medium",
         "reason": "依赖 myFn", "confidence": "high"},
        {"path": ["src/index.vue", "src/modal.vue"], "risk": "high",
         "reason": "直接调用", "confidence": "high"},
    ]
    r.blast_radius_items = [
        BlastRadiusItem(file="src/page.vue", line=22, symbol="myFn",
                        risk="medium", confidence="high", reason="依赖 myFn"),
    ]
    clusters = build_blast_clusters(r)
    assert len(clusters) == 1
    assert clusters[0]["source"]["file"] == "src/index.vue"
    assert clusters[0]["source"]["symbol"] == "myFn"
    nodes = clusters[0]["nodes"]
    assert len(nodes) == 2
    page = next(n for n in nodes if "page.vue" in n["file"])
    assert page["risk"] == "medium"
    assert page["line"] == 22
    assert page["reason"] == "依赖 myFn"


def test_build_blast_clusters_no_impact_paths():
    r = ReviewReport(timestamp="2026-06-16 14:32", diff_summary="")
    r.blast_radius_items = [
        BlastRadiusItem(file="src/a.vue", line=5, symbol="fn",
                        risk="low", confidence="low", reason="低风险")
    ]
    clusters = build_blast_clusters(r)
    assert clusters == []


def test_inject_and_write_embeds_data(tmp_path):
    from web_reporter import inject_and_write
    template = tmp_path / "tmpl.html"
    template.write_text('<html><head></head><body><!--LUNA_DATA_PLACEHOLDER--></body></html>')
    out = tmp_path / "out.html"
    data = {"meta": {"branch": "test"}, "issues": [], "blast": {"clusters": []}}
    inject_and_write(str(template), data, str(out))
    content = out.read_text()
    assert "window.__LUNA_DATA__" in content
    assert '"branch": "test"' in content
    assert "<!--LUNA_DATA_PLACEHOLDER-->" not in content
