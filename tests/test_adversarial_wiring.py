import json
from unittest.mock import patch
from config import Config, DomainEntry
from phases.blast_radius import BlastRadiusItem
from phases.symbol_locator import ChangedSymbol
from phases.context_pack import build_context_pack
from phases.domain_classifier import classify_symbols_by_domain, group_findings_by_domain
from phases.adversarial_verifier import adversarial_verify, build_adversarial_context


PRIVATE_DIFF = (
    "diff --git a/src/private/a.ts b/src/private/a.ts\n"
    "index 0000000..1111111 100644\n"
    "--- a/src/private/a.ts\n"
    "+++ b/src/private/a.ts\n"
    "@@ -1 +1 @@\n-old\n+new\n"
)


def _sym(file: str, symbol: str = "foo") -> ChangedSymbol:
    return ChangedSymbol(file=file, symbol=symbol, symbol_type="function", start_line=1, change_type="modified")


def _item(file: str, symbol: str, risk: str = "high", confidence: str = "medium") -> BlastRadiusItem:
    return BlastRadiusItem(file=file, line=1, symbol=symbol, risk=risk, confidence=confidence, reason="test")


def _cfg_with_domain():
    cfg = Config()
    cfg.domains = [DomainEntry(name="私募", patterns=["src/private*"])]
    return cfg


def _run_adversarial_pass(blast_items, diff, cfg, context_pack):
    """Mirrors the adversarial pass logic in luna.py."""
    if not (cfg.domains and context_pack is not None and blast_items):
        return blast_items
    domain_map = classify_symbols_by_domain(context_pack.changed_symbols, cfg.domains)
    findings_by_domain = group_findings_by_domain(blast_items, domain_map)
    verified = []
    for dname, ditems in findings_by_domain.items():
        uncertain = [i for i in ditems if i.risk == "high" and i.confidence != "high"]
        certain = [i for i in ditems if not (i.risk == "high" and i.confidence != "high")]
        if uncertain:
            domain_files = {s.file for s in domain_map.get(dname, [])}
            ctx = build_adversarial_context(diff, domain_files, context_pack)
            uncertain = adversarial_verify(uncertain, ctx, cfg)
        verified.extend(certain)
        verified.extend(uncertain)
    return verified


def test_adversarial_called_when_domains_configured(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    cfg = _cfg_with_domain()
    sym = _sym("src/private/a.ts", "funcA")
    pack = build_context_pack([sym], [], related_rules=[], related_tests=[])
    items = [_item("src/private/a.ts", "funcA")]
    confirm = json.dumps([{"index": 0, "confirmed": True, "reason": "保留"}])

    with patch("phases.adversarial_verifier.call_claude", return_value=confirm) as mock_adv:
        _run_adversarial_pass(items, PRIVATE_DIFF, cfg, pack)

    mock_adv.assert_called_once()


def test_adversarial_not_called_without_domains():
    cfg = Config()  # no domains
    sym = _sym("src/a.ts", "foo")
    pack = build_context_pack([sym], [], related_rules=[], related_tests=[])
    items = [_item("src/a.ts", "foo")]

    with patch("phases.adversarial_verifier.call_claude") as mock_adv:
        _run_adversarial_pass(items, "", cfg, pack)

    mock_adv.assert_not_called()


def test_adversarial_refuted_finding_removed(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    cfg = _cfg_with_domain()
    sym = _sym("src/private/a.ts", "funcA")
    pack = build_context_pack([sym], [], related_rules=[], related_tests=[])
    items = [_item("src/private/a.ts", "funcA")]
    refute = json.dumps([{"index": 0, "confirmed": False, "reason": "调用方不使用返回值"}])

    with patch("phases.adversarial_verifier.call_claude", return_value=refute):
        result = _run_adversarial_pass(items, PRIVATE_DIFF, cfg, pack)

    assert result == []


def test_adversarial_error_keeps_original(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    cfg = _cfg_with_domain()
    sym = _sym("src/private/a.ts", "funcA")
    pack = build_context_pack([sym], [], related_rules=[], related_tests=[])
    items = [_item("src/private/a.ts", "funcA")]

    with patch("phases.adversarial_verifier.call_claude", side_effect=RuntimeError("timeout")):
        result = _run_adversarial_pass(items, PRIVATE_DIFF, cfg, pack)

    assert len(result) == 1
