"""Pinned GDS jar is local and checksum-verified (no Docker)."""

from __future__ import annotations

import hashlib
from pathlib import Path

from tests.neo4j_gds import GDS_PLUGINS_DIR, gds_jar_path

SHA256SUMS = GDS_PLUGINS_DIR / "SHA256SUMS"


def test_pinned_gds_jar_matches_sha256sums():
    jar = gds_jar_path()
    assert jar.is_file(), f"pinned GDS jar missing: {jar}"
    expected = _expected_sha256(SHA256SUMS, jar.name)
    digest = hashlib.sha256(jar.read_bytes()).hexdigest()
    assert digest.lower() == expected.lower()


def _expected_sha256(sums_path: Path, filename: str) -> str:
    for line in sums_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        digest, name = line.split(None, 1)
        if name.strip() == filename:
            return digest
    raise AssertionError(f"{filename} not listed in {sums_path}")
