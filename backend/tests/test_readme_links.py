"""README relative markdown links must resolve; no leftover milestone1/ paths (F13.5)."""

from __future__ import annotations

import re
from pathlib import Path

README = Path(__file__).resolve().parents[2] / "README.md"
REL_LINK = re.compile(r"\]\((\./[^)\s]+|milestone1/[^)\s]+)(?:\s+\"[^\"]*\")?\)")


def test_readme_has_no_milestone1_folder_paths():
    text = README.read_text(encoding="utf-8")
    assert "milestone1/" not in text


def test_readme_relative_dot_links_exist():
    text = README.read_text(encoding="utf-8")
    missing: list[str] = []
    for match in REL_LINK.finditer(text):
        href = match.group(1)
        target = (README.parent / href).resolve()
        if not target.exists():
            missing.append(href)
    assert missing == [], f"broken README links: {missing}"
