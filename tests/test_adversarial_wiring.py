import json
from unittest.mock import patch, MagicMock
from config import Config, DomainEntry
from phases.blast_radius import BlastRadiusItem
from phases.context_graph import ContextGraph


PRIVATE_DIFF = (
    "diff --git a/src/private/a.ts b/src/private/a.ts\n"
    "index 0000000..1111111 100644\n"
    "--- a/src/private/a.ts\n"
    "+++ b/src/private/a.ts\n"
    "@@ -1 +1 @@\n-old\n+new\n"
)

BLAST_UNCERTAIN = json.dumps([{
    "file": "src/private/a.ts", "line": 1, "symbol": "funcA",
    "risk": "high", "confidence": "medium", "reason": "test",
}])

BLAST_EMPTY = "[]"


def _cfg_with_domain():
    cfg = Config()
    cfg.domains = [DomainEntry(name="私募", patterns=["src/private*"])]
    return cfg


def _empty_graph():
    return ContextGraph()


def test_adversarial_called_when_domains_configured(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    cfg = _cfg_with_domain()
    confirm = json.dumps([{"index": 0, "confirmed": True, "reason": "保留"}])

    with patch("luna.load_graph", return_value=_empty_graph()):
        with patch("luna.build_graph", return_value=_empty_graph()):
            with patch("phases.blast_radius.call_claude", return_value=BLAST_UNCERTAIN):
                with patch("phases.adversarial_verifier.call_claude", return_value=confirm) as mock_adv:
                    from luna import run_review
                    run_review(diff=PRIVATE_DIFF, config=cfg, quiet=True)

    mock_adv.assert_called_once()


def test_adversarial_not_called_without_domains(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    cfg = Config()  # no domains

    with patch("luna.load_graph", return_value=_empty_graph()):
        with patch("luna.build_graph", return_value=_empty_graph()):
            with patch("phases.blast_radius.call_claude", return_value=BLAST_EMPTY):
                with patch("phases.adversarial_verifier.call_claude") as mock_adv:
                    from luna import run_review
                    run_review(diff="diff --git a/a.ts b/a.ts\n@@ -1 +1 @@\n", config=cfg, quiet=True)

    mock_adv.assert_not_called()


def test_adversarial_refuted_finding_removed(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    cfg = _cfg_with_domain()
    refute = json.dumps([{"index": 0, "confirmed": False, "reason": "调用方不使用返回值"}])

    with patch("luna.load_graph", return_value=_empty_graph()):
        with patch("luna.build_graph", return_value=_empty_graph()):
            with patch("phases.blast_radius.call_claude", return_value=BLAST_UNCERTAIN):
                with patch("phases.adversarial_verifier.call_claude", return_value=refute):
                    from luna import run_review
                    report = run_review(diff=PRIVATE_DIFF, config=cfg, quiet=True)

    assert report.blast_radius_items == []


def test_adversarial_error_keeps_original(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    cfg = _cfg_with_domain()

    with patch("luna.load_graph", return_value=_empty_graph()):
        with patch("luna.build_graph", return_value=_empty_graph()):
            with patch("phases.blast_radius.call_claude", return_value=BLAST_UNCERTAIN):
                with patch("phases.adversarial_verifier.call_claude", side_effect=RuntimeError("timeout")):
                    from luna import run_review
                    report = run_review(diff=PRIVATE_DIFF, config=cfg, quiet=True)

    assert len(report.blast_radius_items) == 1
