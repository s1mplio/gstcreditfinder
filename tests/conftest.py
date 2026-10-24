import pytest

from gst_credit_finder import accounts


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
