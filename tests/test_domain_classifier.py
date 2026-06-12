import pytest
from phases.domain_classifier import (
    classify_symbols_by_domain,
    filter_diff_for_files,
    group_findings_by_domain,
)
from phases.symbol_locator import ChangedSymbol
from phases.blast_radius import BlastRadiusItem
from config import DomainEntry


def _sym(file: str, symbol: str = "foo") -> ChangedSymbol:
    return ChangedSymbol(
        file=file, symbol=symbol,
        symbol_type="function", start_line=1, change_type="modified",
    )


def _item(file: str, symbol: str = "foo", risk: str = "high", confidence: str = "medium") -> BlastRadiusItem:
    return BlastRadiusItem(file=file, line=1, symbol=symbol, risk=risk, confidence=confidence, reason="test")


def _domains():
    return [
        DomainEntry(name="私募", patterns=["src/private*", "*/private/*"]),
        DomainEntry(name="公募", patterns=["src/public*", "*/public/*"]),
    ]


# --- classify_symbols_by_domain ---

def test_classify_assigns_correct_domain():
    syms = [_sym("src/private/order.ts"), _sym("src/public/fund.ts")]
    result = classify_symbols_by_domain(syms, _domains())
    assert set(result.keys()) == {"私募", "公募"}
    assert result["私募"][0].file == "src/private/order.ts"


def test_classify_unmatched_goes_to_fallback():
    syms = [_sym("src/shared/utils.ts")]
    result = classify_symbols_by_domain(syms, _domains())
    assert "_unclassified" in result
    assert result["_unclassified"][0].file == "src/shared/utils.ts"


def test_classify_no_domains_all_unclassified():
    syms = [_sym("src/foo.ts")]
    result = classify_symbols_by_domain(syms, [])
    assert result == {"_unclassified": syms}


def test_classify_matches_first_domain_only():
    domains = [
        DomainEntry(name="A", patterns=["src/ab*"]),
        DomainEntry(name="B", patterns=["src/a*"]),
    ]
    result = classify_symbols_by_domain([_sym("src/abc.ts")], domains)
    assert "A" in result and "B" not in result


def test_classify_empty_returns_empty():
    assert classify_symbols_by_domain([], _domains()) == {}


# --- filter_diff_for_files ---

DIFF_TWO_FILES = (
    "diff --git a/src/a.ts b/src/a.ts\n"
    "index 0000000..1111111 100644\n"
    "--- a/src/a.ts\n"
    "+++ b/src/a.ts\n"
    "@@ -1 +1 @@\n-old\n+new\n"
    "diff --git a/src/b.ts b/src/b.ts\n"
    "index 0000000..2222222 100644\n"
    "--- a/src/b.ts\n"
    "+++ b/src/b.ts\n"
    "@@ -1 +1 @@\n-old\n+new\n"
)


def test_filter_diff_returns_only_matching_file():
    filtered = filter_diff_for_files(DIFF_TWO_FILES, {"src/b.ts"})
    assert "b/src/b.ts" in filtered
    assert "b/src/a.ts" not in filtered


def test_filter_diff_no_match_returns_empty():
    assert filter_diff_for_files(DIFF_TWO_FILES, {"src/c.ts"}) == ""


def test_filter_diff_empty_files_returns_empty():
    assert filter_diff_for_files(DIFF_TWO_FILES, set()) == ""


# --- group_findings_by_domain ---

def test_group_findings_maps_to_correct_domain():
    syms = [_sym("src/private/a.ts", "funcA"), _sym("src/public/b.ts", "funcB")]
    domain_map = classify_symbols_by_domain(syms, _domains())
    items = [_item("src/private/a.ts", "funcA"), _item("src/public/b.ts", "funcB")]
    result = group_findings_by_domain(items, domain_map)
    assert result["私募"][0].symbol == "funcA"
    assert result["公募"][0].symbol == "funcB"


def test_group_findings_unmatched_file_to_unclassified():
    domain_map = {"私募": [_sym("src/private/a.ts", "funcA")]}
    items = [_item("src/other/x.ts", "funcX")]
    result = group_findings_by_domain(items, domain_map)
    assert "_unclassified" in result
    assert result["_unclassified"][0].symbol == "funcX"


def test_group_findings_empty_items_returns_empty():
    domain_map = {"私募": [_sym("src/private/a.ts")]}
    assert group_findings_by_domain([], domain_map) == {}
