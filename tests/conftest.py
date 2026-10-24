import subprocess
import sys
from pathlib import Path

import pytest

from gst_credit_finder import accounts

FIXTURES = Path(__file__).resolve().parent / "fixtures"
SAMPLE = FIXTURES / "data"


@pytest.fixture(autouse=True, scope="session")
def sample_files():
    """Fictional register + GSTR-2B files in tests/fixtures/data (git-ignored)."""
    subprocess.run([sys.executable, str(FIXTURES / "generate_sample.py"), str(SAMPLE)], check=True)


@pytest.fixture(autouse=True, scope="session")
def temporary_database(tmp_path_factory):
    """Never touch the real database during tests."""
    accounts.DB_PATH = tmp_path_factory.mktemp("db") / "test.db"
    yield


@pytest.fixture(autouse=True)
def reset_login_throttle():
    from gst_credit_finder import web

    web._failed_logins.clear()
    yield
