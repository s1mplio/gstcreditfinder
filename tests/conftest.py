"""Shared test setup.

Tests use a throwaway SQLite file. To run the same tests on Postgres:

    $env:TEST_DATABASE_URL = "postgresql://postgres:test@localhost:5433/postgres"
    python -m pytest -q

(Every table in that database is dropped first; never point it at real data.)
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest

from gst_credit_finder import db

FIXTURES = Path(__file__).resolve().parent / "fixtures"
SAMPLE = FIXTURES / "data"
POSTGRES = os.getenv("TEST_DATABASE_URL", "")

TABLES = (
    "attempts", "bills", "nudges", "password_resets", "reminders",
    "checks", "businesses", "sessions", "users",
)


@pytest.fixture(autouse=True, scope="session")
def sample_files():
    """Fictional register + GSTR-2B files in tests/fixtures/data (git-ignored)."""
    subprocess.run([sys.executable, str(FIXTURES / "generate_sample.py"), str(SAMPLE)], check=True)


@pytest.fixture(autouse=True, scope="session")
def temporary_database(tmp_path_factory):
    """Never touch the real database during tests."""
    if POSTGRES:
        db.DATABASE_URL = POSTGRES
        with db.connect() as conn:
            conn.execute("DROP TABLE IF EXISTS " + ", ".join(TABLES) + " CASCADE")
        db._ready.clear()
    else:
        db.DATABASE_URL = ""
        db.DB_PATH = tmp_path_factory.mktemp("db") / "test.db"
    yield


@pytest.fixture(autouse=True)
def reset_login_throttle():
    with db.connect() as conn:
        conn.execute("DELETE FROM attempts")
    yield


def empty_database(tmp_path, monkeypatch):
    """A database with no users, for tests that look at everyone."""
    if POSTGRES:
        with db.connect() as conn:
            conn.execute("DELETE FROM users")  # cascades to everything else
    else:
        monkeypatch.setattr(db, "DB_PATH", tmp_path / "fresh.db")
