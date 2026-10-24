import json
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from gst_credit_finder import accounts
from gst_credit_finder.web import app

ROOT = Path(__file__).resolve().parents[1]
SAMPLE = ROOT / "sample_data"

PASSWORD = "correct horse"


@pytest.fixture(scope="module", autouse=True)
def sample_data():
    subprocess.run([sys.executable, str(SAMPLE / "generate_sample.py")], check=True)


def signup(login, role="shop", **extra):
    session = TestClient(app)
    body = {"name": "Owner", "login": login, "password": PASSWORD, "role": role, **extra}
    if role == "shop":
        body.setdefault("business_name", "My Shop")
    return session, session.post("/api/signup", json=body)


def upload(session, business_id, periods=("092026", "102026")):
    files = [("register", ("bills.csv", (SAMPLE / "purchase_register.csv").read_bytes(), "text/csv"))]
    files += [
        ("gstr2b", (f"gstr2b_{p}.json", (SAMPLE / f"gstr2b_{p}.json").read_bytes(), "application/json"))
        for p in periods
    ]
    return session.post("/api/check", files=files, data={"business_id": business_id})


def sample_buyer_gstin():
    data = json.loads((SAMPLE / "gstr2b_092026.json").read_text(encoding="utf-8"))
    return data["data"]["gstin"]


# ---------------------------------------------------------
# Sign-up + login
# ---------------------------------------------------------

def test_login_ids_are_normalised():
    assert accounts.normalize_login("+91 98765-43210") == "9876543210"
    assert accounts.normalize_login(" Owner@Example.COM ") == "owner@example.com"

    for bad in ("12345", "not an email@", "5876543210"):
        with pytest.raises(accounts.AccountError):
            accounts.normalize_login(bad)


def test_passwords_are_hashed_not_stored():
    stored = accounts.hash_password(PASSWORD)
    assert PASSWORD not in stored
    assert accounts.check_password(PASSWORD, stored)
    assert not accounts.check_password("wrong password", stored)


def test_shopkeeper_signup_creates_shop_and_logs_in():
    session, response = signup("9811100001", business_name="Ganesh Stores")
    assert response.status_code == 200, response.text

    me = session.get("/api/me").json()
    assert me["user"]["role"] == "shop"
    assert [b["name"] for b in me["businesses"]] == ["Ganesh Stores"]
    assert "password" not in json.dumps(me)


def test_signup_errors_are_clear_and_leave_no_account():
    _, response = signup("9811100002", business_gstin="27AAAAA0000A1ZZ")
    assert response.status_code == 400
    assert "GSTIN" in response.json()["detail"]

    _, response = signup("9811100002", password="short")
    assert response.status_code == 400

    # The first failed sign-up must not have reserved the number.
    _, response = signup("9811100002")
    assert response.status_code == 200

    _, response = signup("+91 98111 00002")
    assert response.status_code == 400
    assert "already exists" in response.json()["detail"]


def test_login_logout_and_wrong_password():
    signup("owner@example.com", role="ca")

    session = TestClient(app)
    assert session.get("/api/me").status_code == 401

    wrong = session.post("/api/login", json={"login": "owner@example.com", "password": "nope"})
    assert wrong.status_code == 401

    right = session.post("/api/login", json={"login": "OWNER@example.com", "password": PASSWORD})
    assert right.status_code == 200
    assert right.json()["user"]["role"] == "ca"
    assert session.get("/api/me").status_code == 200

    session.post("/api/logout")
    assert session.get("/api/me").status_code == 401


def test_repeated_wrong_passwords_are_blocked():
    signup("9811100003")
    session = TestClient(app)

    for _ in range(10):
        session.post("/api/login", json={"login": "9811100003", "password": "guess"})

    blocked = session.post("/api/login", json={"login": "9811100003", "password": PASSWORD})
    assert blocked.status_code == 429


def test_requests_from_other_websites_are_refused():
    session, _ = signup("9811100004")
    response = session.post(
        "/api/businesses",
        json={"name": "Evil"},
        headers={"Origin": "https://evil.example"},
    )
    assert response.status_code == 403


# ---------------------------------------------------------
# Checks, history, recovery
# ---------------------------------------------------------

def test_check_is_saved_and_report_downloadable():
    session, response = signup("9811100010")
    business_id = response.json()["businesses"][0]["id"]

    assert upload(TestClient(app), business_id).status_code == 401

    response = upload(session, business_id)
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["total_at_risk"] > 0
    assert data["recovered"]["amount"] == 0

    history = session.get(f"/api/businesses/{business_id}/checks").json()
    assert [c["id"] for c in history["checks"]] == [data["check_id"]]

    # The business GSTIN was filled in from GSTR-2B.
    assert history["business"]["gstin"] == sample_buyer_gstin()

    report = session.get(f"/api/checks/{data['check_id']}/report")
    assert report.status_code == 200
    assert report.content[:2] == b"PK"

    assert session.delete(f"/api/checks/{data['check_id']}").status_code == 200
    assert session.get(f"/api/businesses/{business_id}/checks").json()["checks"] == []


def test_next_check_shows_credit_recovered():
    session, response = signup("9811100011")
    business_id = response.json()["businesses"][0]["id"]

    first = upload(session, business_id, periods=("092026",)).json()
    second = upload(session, business_id).json()

    # Suppliers who filed late appear in the October GSTR-2B.
    assert second["total_at_risk"] < first["total_at_risk"]
    assert second["recovered"]["amount"] > 0
    assert second["recovered"]["items"]

    listed = session.get("/api/me").json()["businesses"][0]
    assert listed["last_check_id"] == second["check_id"]
    assert listed["recovered"] == second["recovered"]["amount"]


def test_recovered_since_counts_only_bills_that_were_at_risk():
    def result(number, status, at_risk):
        return {
            "supplier_gstin": "27AFSPS7316K1ZN",
            "supplier_name": "Sharma Traders",
            "bill_number": number,
            "gstr2b_number": number,
            "status": status,
            "at_risk": at_risk,
        }

    previous = {"results": [result("ST/1", "missing_in_2b", 900.0), result("ST/2", "matched", None)]}
    current = {"results": [result("st-1", "matched", None), result("ST/2", "matched", None)]}

    recovered = accounts.recovered_since(previous, current)
    assert recovered["amount"] == 900.0
    assert [i["bill_number"] for i in recovered["items"]] == ["st-1"]

    assert accounts.recovered_since(None, current)["amount"] == 0


def test_reminder_status_is_tracked_per_supplier():
    session, response = signup("9811100012")
    business_id = response.json()["businesses"][0]["id"]
    data = upload(session, business_id).json()
    supplier = data["reminders"][0]["supplier_gstin"]

    response = session.put(
        f"/api/businesses/{business_id}/reminders/{supplier}", json={"status": "sent"}
    )
    assert response.status_code == 200
    assert response.json()[supplier]["status"] == "sent"

    reopened = session.get(f"/api/checks/{data['check_id']}").json()
    assert reopened["reminder_status"][supplier]["status"] == "sent"

    bad = session.put(f"/api/businesses/{business_id}/reminders/{supplier}", json={"status": "x"})
    assert bad.status_code == 400


# ---------------------------------------------------------
# CA with clients, and keeping accounts apart
# ---------------------------------------------------------

def test_ca_manages_clients_and_wrong_client_upload_is_caught():
    ca, response = signup("ca@example.com", role="ca")
    assert response.json()["businesses"] == []

    right = ca.post("/api/businesses", json={"name": "Client A", "gstin": sample_buyer_gstin()}).json()
    wrong = ca.post("/api/businesses", json={"name": "Client B", "gstin": "27AFSPS7316K1ZN"}).json()

    duplicate = ca.post("/api/businesses", json={"name": "Again", "gstin": sample_buyer_gstin()})
    assert duplicate.status_code == 400

    assert upload(ca, right["id"]).status_code == 200

    mismatch = upload(ca, wrong["id"])
    assert mismatch.status_code == 400
    assert "Choose the right business" in mismatch.json()["detail"]

    names = [b["name"] for b in ca.get("/api/me").json()["businesses"]]
    assert names == ["Client A", "Client B"]


def test_users_cannot_see_each_others_data():
    owner, response = signup("9811100020")
    business_id = response.json()["businesses"][0]["id"]
    check_id = upload(owner, business_id).json()["check_id"]

    other, _ = signup("9811100021")

    assert other.get(f"/api/businesses/{business_id}/checks").status_code == 404
    assert other.get(f"/api/checks/{check_id}").status_code == 404
    assert other.get(f"/api/checks/{check_id}/report").status_code == 404
    assert other.delete(f"/api/checks/{check_id}").status_code == 404
    assert other.delete(f"/api/businesses/{business_id}").status_code == 404
    assert upload(other, business_id).status_code == 404
    assert other.put(
        f"/api/businesses/{business_id}/reminders/27AFSPS7316K1ZN", json={"status": "sent"}
    ).status_code == 404

    assert owner.get(f"/api/checks/{check_id}").status_code == 200


def test_delete_account_removes_everything():
    session, response = signup("9811100030")
    business_id = response.json()["businesses"][0]["id"]
    check_id = upload(session, business_id).json()["check_id"]

    assert session.delete("/api/me").status_code == 200
    assert session.get("/api/me").status_code == 401

    with accounts.connect() as db:
        assert db.execute("SELECT 1 FROM checks WHERE id = ?", (check_id,)).fetchone() is None
        assert db.execute("SELECT 1 FROM businesses WHERE id = ?", (business_id,)).fetchone() is None

    again = TestClient(app).post("/api/login", json={"login": "9811100030", "password": PASSWORD})
    assert again.status_code == 401
