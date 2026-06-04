from pathlib import Path
from test_importer import parse_test_file, find_related_tests


SAMPLE_SPEC = """\
import { describe, it, expect } from 'vitest'

describe('useAuth', () => {
  it('should refresh token on expiry', () => {
    expect(true).toBe(true)
  })

  it('should clear session on logout', () => {
    expect(true).toBe(true)
  })
})

describe('Login page', () => {
  it('renders login form', () => {})
})
"""

SAMPLE_DIFF = "diff --git a/src/composables/useAuth.js b/src/composables/useAuth.js\n+++ b/src/composables/useAuth.js\n"


def test_parses_test_cases(tmp_path):
    f = tmp_path / "useAuth.spec.ts"
    f.write_text(SAMPLE_SPEC)
    cases = parse_test_file(str(f))
    assert len(cases) == 3
    its = [c.it for c in cases]
    assert "should refresh token on expiry" in its
    assert "should clear session on logout" in its


def test_parse_empty_file(tmp_path):
    f = tmp_path / "empty.spec.ts"
    f.write_text("")
    assert parse_test_file(str(f)) == []


def test_parse_nonexistent_file():
    assert parse_test_file("/nonexistent/file.spec.ts") == []


def test_find_related_tests(tmp_path):
    f = tmp_path / "useAuth.spec.ts"
    f.write_text(SAMPLE_SPEC)
    all_cases = parse_test_file(str(f))
    related = find_related_tests(all_cases, SAMPLE_DIFF)
    assert len(related) > 0
    assert all("useauth" in c.describe.lower() or "useauth" in c.it.lower() for c in related)


def test_find_related_no_match(tmp_path):
    f = tmp_path / "unrelated.spec.ts"
    f.write_text("describe('Other', () => { it('does nothing', () => {}) })")
    cases = parse_test_file(str(f))
    related = find_related_tests(cases, SAMPLE_DIFF)
    assert related == []
