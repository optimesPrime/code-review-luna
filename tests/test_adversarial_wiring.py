import json
from unittest.mock import patch
from config import Config
from phases.blast_radius import BlastRadiusItem
from phases.context_pack import build_context_pack
from phases.symbol_locator import ChangedSymbol
from phases.adversarial_verifier import (
    adversarial_verify,
    build_adversarial_context,
    filter_diff_for_files,
)


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


def _run_adversarial_pass(blast_items, diff, cfg, context_pack):
    """Mirrors the adversarial pass in luna.py."""
    if not (context_pack is not None and blast_items):
        return blast_items, []
    uncertain = [i for i in blast_items if i.risk == "high" and i.confidence != "high"]
    certain = [i for i in blast_items if not (i.risk == "high" and i.confidence != "high")]
    if uncertain:
        files = {i.file for i in uncertain}
        ctx = build_adversarial_context(diff, files, context_pack)
        uncertain, refuted = adversarial_verify(uncertain, ctx, cfg)
    else:
        refuted = []
    return certain + uncertain, refuted


def test_adversarial_called_for_uncertain_high(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    cfg = Config()  # no domains needed
    sym = _sym("src/private/a.ts", "funcA")
    pack = build_context_pack([sym], [], related_rules=[], related_tests=[])
    items = [_item("src/private/a.ts", "funcA")]
    confirm = json.dumps([{"index": 0, "confirmed": True, "reason": "保留"}])

    with patch("phases.adversarial_verifier.call_claude", return_value=confirm) as mock_adv:
        _run_adversarial_pass(items, PRIVATE_DIFF, cfg, pack)

    mock_adv.assert_called_once()


def test_adversarial_not_called_for_high_confidence():
    cfg = Config()
    sym = _sym("src/a.ts", "foo")
    pack = build_context_pack([sym], [], related_rules=[], related_tests=[])
    items = [_item("src/a.ts", "foo", risk="high", confidence="high")]

    with patch("phases.adversarial_verifier.call_claude") as mock_adv:
        _run_adversarial_pass(items, "", cfg, pack)

    mock_adv.assert_not_called()


def test_adversarial_refuted_finding_removed(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    cfg = Config()
    sym = _sym("src/private/a.ts", "funcA")
    pack = build_context_pack([sym], [], related_rules=[], related_tests=[])
    items = [_item("src/private/a.ts", "funcA")]
    refute = json.dumps([{"index": 0, "confirmed": False, "reason": "调用方不使用返回值"}])

    with patch("phases.adversarial_verifier.call_claude", return_value=refute):
        survivors, refuted = _run_adversarial_pass(items, PRIVATE_DIFF, cfg, pack)

    assert survivors == []
    assert len(refuted) == 1


def test_adversarial_error_keeps_original(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    cfg = Config()
    sym = _sym("src/private/a.ts", "funcA")
    pack = build_context_pack([sym], [], related_rules=[], related_tests=[])
    items = [_item("src/private/a.ts", "funcA")]

    with patch("phases.adversarial_verifier.call_claude", side_effect=RuntimeError("timeout")):
        survivors, refuted = _run_adversarial_pass(items, PRIVATE_DIFF, cfg, pack)

    assert len(survivors) == 1
