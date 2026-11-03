"""Shared fixtures: keep tests away from the studio's real database and exports."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modules.database import Database  # noqa: E402


@pytest.fixture()
def temp_db(tmp_path) -> Database:
    """A throwaway SQLite database with the production schema and seeded sources."""
    return Database(tmp_path / "studio.db")
