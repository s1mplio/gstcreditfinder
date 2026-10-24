import io
import zipfile
from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from openpyxl import Workbook

from gst_credit_finder import accounts, backup, notify, nudges
from gst_credit_finder.web import app

ROOT = Path(__file__).resolve().parents[1]
SAMPLE = ROOT / "tests" / "fixtures" / "data"
PASSWORD = "correct horse"


@pytest.fixture(autouse=True)
def no_real_messages(monkeypatch):
    for name in ("SMTP_HOST", "MAIL_FROM", "MSG91_AUTH_KEY"):
        monkeypatch.delenv(name, raising=False)
    notify.outbox.clear()


@pytest.fixture
def fresh_db(tmp_path, monkeypatch):
    monkeypatch.setattr(accounts, "DB_PATH", tmp_path / "fresh.db")
    monkeypatch.setenv("BACKUP_DIR", str(tmp_path / "backups"))
    return tmp_path


def signup(login, role="shop"):
    session = TestClient(app)
    body = {"name": "Ravi Kumar", "login": login, "password": PASSWORD, "role": role}
    if role == "shop":
        body["business_name"] = "Ravi Kirana"
    response = session.post("/api/signup", json=body)
    assert response.status_code == 200, response.text
    businesses = response.json()["businesses"]
    return session, businesses[0]["id"] if businesses else None


def upload(session, business_id, gstr2b_files):
    files = [("register", ("bills.csv", (SAMPLE / "purchase_register.csv").read_bytes(), "text/csv"))]
    files += [("gstr2b", f) for f in gstr2b_files]
    return session.post("/api/check", files=files, data={"business_id": business_id})


def sample_2b(period):
    path = SAMPLE / f"gstr2b_{period}.json"
    return (path.name, path.read_bytes(), "application/json")


# ---------------------------------------------------------
# Forgot password
# ---------------------------------------------------------

def test_password_reset_with_code():
    old_session, _ = signup("9822200001")

    client = TestClient(app)
    assert client.post("/api/password/forgot", json={"login": "+91 98222 00001"}).json() == {"ok": True}

    sent = notify.outbox[-1]
    assert sent["to"] == "9822200001"
    code = sent["vars"]["otp"]
    assert len(code) == 6 and code in sent["text"]

    response = client.post(
        "/api/password/reset",
        json={"login": "9822200001", "code": code, "password": "brand new pass"},
    )
    assert response.status_code == 200
    assert client.get("/api/me").status_code == 200

    # The old session (e.g. a lost phone) is logged out; the code is single-use.
    assert old_session.get("/api/me").status_code == 401
    again = client.post("/api/password/reset", json={"login": "9822200001", "code": code, "password": "another one"})
    assert again.status_code == 400

    login = TestClient(app).post("/api/login", json={"login": "9822200001", "password": "brand new pass"})
    assert login.status_code == 200


def test_reset_gives_the_same_answer_for_unknown_accounts():
    response = TestClient(app).post("/api/password/forgot", json={"login": "nobody@example.com"})
    assert response.json() == {"ok": True}
    assert not notify.outbox


def test_reset_code_attempts_are_limited():
    signup("9822200002")
    client = TestClient(app)
    client.post("/api/password/forgot", json={"login": "9822200002"})
    code = notify.outbox[-1]["vars"]["otp"]
    wrong = "000000" if code != "000000" else "111111"

    for _ in range(accounts.RESET_MAX_ATTEMPTS):
        response = client.post("/api/password/reset", json={"login": "9822200002", "code": wrong, "password": "x" * 10})
        assert response.status_code == 400

    # Even the right code no longer works after too many wrong tries.
    response = client.post("/api/password/reset", json={"login": "9822200002", "code": code, "password": "x" * 10})
    assert response.status_code == 400


def test_reset_codes_are_not_resent_immediately():
    signup("9822200003")
    client = TestClient(app)
    client.post("/api/password/forgot", json={"login": "9822200003"})
    client.post("/api/password/forgot", json={"login": "9822200003"})
    assert len(notify.outbox) == 1


def test_email_logins_get_codes_by_email_text():
    signup("ravi@example.com")
    TestClient(app).post("/api/password/forgot", json={"login": "Ravi@Example.com"})
    assert notify.outbox[-1]["to"] == "ravi@example.com"
    assert "expires in 10 minutes" in notify.outbox[-1]["text"]


# ---------------------------------------------------------
# GSTR-2B uploads: ZIP, PDF, Excel
# ---------------------------------------------------------

def zipped(files):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, content in files:
            archive.writestr(name, content)
    return buffer.getvalue()


def test_gstr2b_zip_from_the_portal_is_accepted():
    session, business_id = signup("9822200010")
    archive = zipped([(name, content) for name, content, _ in (sample_2b("092026"), sample_2b("102026"))])

    via_zip = upload(session, business_id, [("returns_r2b.zip", archive, "application/zip")])
    assert via_zip.status_code == 200, via_zip.text

    via_json = upload(session, business_id, [sample_2b("092026"), sample_2b("102026")])
    assert via_zip.json()["total_at_risk"] == via_json.json()["total_at_risk"]


@pytest.mark.parametrize(
    "case, content, message",
    [
        (1, b"%PDF-1.7 ...", "PDF version"),
        (2, None, "Excel version"),
        (3, zipped([("readme.txt", b"hello")]), "no GSTR-2B JSON"),
        (4, b"PK\x03\x04 broken", "damaged ZIP"),
    ],
)
def test_wrong_gstr2b_downloads_get_clear_errors(case, content, message):
    if content is None:
        buffer = io.BytesIO()
        Workbook().save(buffer)
        content = buffer.getvalue()

    session, business_id = signup(f"982220011{case}")
    response = upload(session, business_id, [("gstr2b_download", content, "application/octet-stream")])
    assert response.status_code == 400
    assert message in response.json()["detail"]


# ---------------------------------------------------------
# GSTR-3B comparison data
# ---------------------------------------------------------

def test_check_reports_2b_credit_per_period_by_tax_head():
    session, business_id = signup("9822200020")
    data = upload(session, business_id, [sample_2b("092026"), sample_2b("102026")]).json()

    periods = {row["period"]: row for row in data["itc_2b"]}
    assert set(periods) == {"092026", "102026"}

    september = periods["092026"]
    assert september["cgst"] == september["sgst"] > 0
    assert set(september) == {"period", "igst", "cgst", "sgst", "cess"}

    # Saved with the check, so reopening a past check can still compare.
    reopened = session.get(f"/api/checks/{data['check_id']}").json()
    assert reopened["itc_2b"] == data["itc_2b"]


# ---------------------------------------------------------
# Monthly GSTR-2B reminders
# ---------------------------------------------------------

def test_gstr2b_is_ready_from_the_14th():
    assert nudges.latest_ready_period(date(2026, 11, 13)) == (2026, 9)
    assert nudges.latest_ready_period(date(2026, 11, 14)) == (2026, 10)
    assert nudges.latest_ready_period(date(2027, 1, 20)) == (2026, 12)
    assert nudges.latest_ready_period(date(2027, 2, 2)) == (2026, 12)


def test_reminders_go_once_to_people_with_unchecked_businesses(fresh_db):
    shop, shop_business = signup("9822200030")
    ca, _ = signup("ca-nudge@example.com", role="ca")
    for name in ("Client A", "Client B"):
        ca.post("/api/businesses", json={"name": name})
    signup("9822200031")  # will opt out
    quiet, _ = signup("9822200032")
    quiet.put("/api/me/settings", json={"notify": False})
    signup_ca_without_clients = signup("empty-ca@example.com", role="ca")
    assert signup_ca_without_clients[1] is None

    # The shop has already checked September.
    upload(shop, shop_business, [sample_2b("092026")])

    sent = nudges.send_due(date(2026, 10, 20))  # September 2B is out
    recipients = {m["to"]: m for m in notify.outbox}

    assert "9822200030" not in recipients  # already checked
    assert "9822200032" not in recipients  # opted out
    assert "empty-ca@example.com" not in recipients  # nothing to check
    assert "ca-nudge@example.com" in recipients and "9822200031" in recipients
    assert sent == 2
    assert "September 2026" in recipients["ca-nudge@example.com"]["text"]
    assert "2 businesses" in recipients["ca-nudge@example.com"]["text"]

    # Running again the same month sends nothing new.
    assert nudges.send_due(date(2026, 10, 25)) == 0

    # October's 2B: the shop hasn't checked it yet, so now it's reminded too.
    nudges.send_due(date(2026, 11, 14))
    assert any(m["to"] == "9822200030" and "October 2026" in m["text"] for m in notify.outbox)


def test_settings_toggle_is_saved():
    session, _ = signup("9822200040")
    assert session.get("/api/me").json()["user"]["notify"] is True
    assert session.put("/api/me/settings", json={"notify": False}).json()["user"]["notify"] is False
    assert session.get("/api/me").json()["user"]["notify"] is False


# ---------------------------------------------------------
# Backups + pages
# ---------------------------------------------------------

def test_backup_copies_database_and_keeps_newest(fresh_db, monkeypatch):
    signup("9822200050")
    monkeypatch.setenv("BACKUP_KEEP", "2")

    folder = fresh_db / "backups"
    for index in range(3):
        stale = folder / f"{backup.PREFIX}2020010{index}-000000.db"
        folder.mkdir(exist_ok=True)
        stale.write_bytes(b"old")

    target = backup.run_backup()
    remaining = sorted(p.name for p in folder.glob("*.db"))

    assert len(remaining) == 2 and target.name in remaining

    import sqlite3
    with sqlite3.connect(target) as copy:
        assert copy.execute("SELECT login FROM users").fetchall() == [("9822200050",)]


def test_privacy_and_terms_pages():
    client = TestClient(app)
    assert "Privacy Policy" in client.get("/privacy").text
    assert "Terms of Use" in client.get("/terms").text
    assert 'href="/privacy"' in client.get("/").text
