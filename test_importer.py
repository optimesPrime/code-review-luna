from __future__ import annotations
import re
from dataclasses import dataclass
from pathlib import Path


@dataclass
class TestCase:
    file: str
    describe: str
    it: str
    line: int


def parse_test_file(file_path: str) -> list[TestCase]:
    p = Path(file_path)
    if not p.exists():
        return []

    lines = p.read_text(encoding="utf-8").split("\n")
    results: list[TestCase] = []
    current_describe = ""

    for i, line in enumerate(lines, 1):
        dm = re.search(r"describe\s*\(\s*['\"`](.+?)['\"`]", line)
        if dm:
            current_describe = dm.group(1)
            continue
        im = re.search(r"(?:it|test)\s*\(\s*['\"`](.+?)['\"`]", line)
        if im:
            results.append(TestCase(
                file=file_path,
                describe=current_describe,
                it=im.group(1),
                line=i,
            ))

    return results


def find_related_tests(test_cases: list[TestCase], diff: str) -> list[TestCase]:
    changed_stems: set[str] = set()
    for line in diff.split("\n"):
        if line.startswith("+++ b/"):
            stem = Path(line[6:]).stem.lower()
            stem = re.sub(r"\.(spec|test)$", "", stem)
            changed_stems.add(stem)

    related: list[TestCase] = []
    for tc in test_cases:
        for stem in changed_stems:
            if stem in tc.describe.lower() or stem in tc.it.lower():
                related.append(tc)
                break

    return related
