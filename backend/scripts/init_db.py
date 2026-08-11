"""Standalone Neo4j schema bootstrap (also used by FastAPI startup when AUTO_MIGRATE=true)."""

from __future__ import annotations

import sys
from pathlib import Path

# Allow `python scripts/init_db.py` from the backend/ directory.
BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.db.schema import SCHEMA_PATH, apply_schema  # noqa: E402


def main() -> None:
    count = apply_schema()
    print(f"Applied {count} schema statements from {SCHEMA_PATH}")


if __name__ == "__main__":
    main()
