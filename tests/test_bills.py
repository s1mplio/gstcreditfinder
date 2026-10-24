import csv
import subprocess
import sys
from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from gst_credit_finder import bills
from gst_credit_finder.accounts import AccountError
from gst_credit_finder.web import app

ROOT = Path(__file__).resolve().parents[1]
SAMPLE = ROOT / "sample_data"
TODAY = date(2026, 10, 24)
SHOP_GSTIN = "27ABKPG4821M1ZF"  # Maharashtra, like the sample shop


@pytest.fixture(scope="module", autouse=True)
def sample_data():
    subprocess.run([sys.executable, str(SAMPLE / "generate_sample.py")], check=True)


def good_bill(**changes):
    bill = {
        "supplier_gstin": "27AFSPS7316K1ZN",
        "supplier_name": "Sharma Traders",
        "invoice_number": "ST-1201",
        "invoice_date": "2026-09-02",
        "taxable_value": "36700",
        "igst": "0",
        "cgst": "917.50",
        "sgst": "917.50",
        "cess": "0",
        "invoice_value": "38535.00",
    }
    bill.update(changes)
    return bill


def signup(login):
    session = TestClient(app)
    response = session.post(
        "/api/signup",
        json={
            "name": "Ravi",
            "login": login,
            "password": "correct horse",
            "role": "shop",
            "business_name": "Ravi Kirana",
            "business_gstin": SHOP_GSTIN,
        },
    )
    assert response.status_code == 200, response.text
    return session, response.json()["businesses"][0]["id"]


# ---------------------------------------------------------
# Checking a typed bill
# ---------------------------------------------------------

def test_a_correct_bill_has_no_warnings():
    bill, warnings = bills.validate(good_bill(), SHOP_GSTIN, TODAY)
    assert warnings == []
    assert bill["supplier_gstin"] == "27AFSPS7316K1ZN"
    assert bill["cgst"] == "917.50"


def test_total_is_filled_in_when_left_blank():
    bill, warnings = bills.validate(good_bill(invoice_value=""), SHOP_GSTIN, TODAY)
    assert bill["invoice_value"] == "38535.00" and not warnings


@pytest.mark.parametrize(
    "changes, expected",
    [
        ({"supplier_gstin": "27AFSPS7316K1ZM"}, "check digit"),
        ({"invoice_value": "40000"}, "bill total"),
        ({"cgst": "0", "sgst": "0", "invoice_value": ""}, "No GST"),
        ({"cgst": "917.50", "sgst": "900", "invoice_value": ""}, "CGST and SGST"),
        ({"cgst": "1000", "sgst": "1000", "invoice_value": ""}, "not a standard rate"),
        ({"igst": "1835", "cgst": "0", "sgst": "0"}, "should have CGST + SGST"),
        ({"supplier_gstin": "24AACFG6187Q1ZT"}, "should have IGST"),
    ],
)
def test_likely_typos_give_warnings(changes, expected):
    _, warnings = bills.validate(good_bill(**changes), SHOP_GSTIN, TODAY)
    assert any(expected in warning for warning in warnings), warnings


@pytest.mark.parametrize(
    "changes, expected",
    [
        ({"supplier_gstin": ""}, "GSTIN"),
        ({"invoice_number": " "}, "invoice number"),
        ({"invoice_date": ""}, "invoice date"),
        ({"invoice_date": "2026-12-01"}, "future"),
        ({"taxable_value": "0"}, "taxable value"),
        ({"taxable_value": "abc"}, "Taxable value"),
        ({"cgst": "-5"}, "negative"),
    ],
)
def test_bills_that_cannot_be_saved(changes, expected):
    with pytest.raises(AccountError, match=expected):
        bills.validate(good_bill(**changes), SHOP_GSTIN, TODAY)


def test_mixed_rate_bill_is_only_a_warning():
    # 10,000 at 5% + 10,000 at 18% on one bill
    bill = good_bill(taxable_value="20000", cgst="1150", sgst="1150", invoice_value="22300")
    _, warnings = bills.validate(bill, SHOP_GSTIN, TODAY)
    assert len(warnings) == 1 and "different rates" in warnings[0]


# ---------------------------------------------------------
# Saving and listing
# ---------------------------------------------------------

def test_add_list_and_delete_bills():
    session, business_id = signup("9833300001")
    url = f"/api/businesses/{business_id}/bills"

    assert session.post(url, json=good_bill()).json()["saved"] is True

    # Same supplier + same invoice number written differently is a duplicate.
    duplicate = session.post(url, json=good_bill(invoice_number="st/1201"))
    assert duplicate.status_code == 400
    assert "already saved" in duplicate.json()["detail"]

    session.post(url, json=good_bill(invoice_number="ST-1202", invoice_date="2026-08-30"))

    listed = session.get(url).json()
    assert listed["month"] == "2026-09"  # newest month with bills
    assert [m["month"] for m in listed["months"]] == ["2026-09", "2026-08"]
    assert [b["invoice_number"] for b in listed["bills"]] == ["ST-1201"]
    assert listed["bills"][0]["gst"] == 1835.0
    assert listed["suppliers"] == [{"supplier_gstin": "27AFSPS7316K1ZN", "supplier_name": "Sharma Traders"}]

    august = session.get(f"{url}?month=2026-08").json()["bills"]
    assert session.delete(f"{url}/{august[0]['id']}").status_code == 200
    assert session.get(f"{url}?month=2026-08").json()["bills"] == []


def test_suspicious_bill_needs_confirmation():
    session, business_id = signup("9833300002")
    url = f"/api/businesses/{business_id}/bills"
    typo = good_bill(supplier_gstin="27AFSPS7316K1ZM")

    first = session.post(url, json=typo)
    assert first.status_code == 409
    assert first.json()["saved"] is False
    assert session.get(url).json()["bills"] == []

    second = session.post(url, json={**typo, "confirm": True})
    assert second.status_code == 200 and second.json()["saved"] is True


def test_other_users_cannot_touch_bills():
    owner, business_id = signup("9833300003")
    url = f"/api/businesses/{business_id}/bills"
    bill_id = owner.post(url, json=good_bill()).json()["bill"]["id"]

    other, _ = signup("9833300004")
    assert other.get(url).status_code == 404
    assert other.post(url, json=good_bill(invoice_number="X1")).status_code == 404
    assert other.delete(f"{url}/{bill_id}").status_code == 404


# ---------------------------------------------------------
# Running a check on saved bills
# ---------------------------------------------------------

def gstr2b_files():
    return [
        ("gstr2b", (path.name, path.read_bytes(), "application/json"))
        for path in sorted(SAMPLE.glob("gstr2b_*.json"))
    ]


def register_file():
    return [("register", ("bills.csv", (SAMPLE / "purchase_register.csv").read_bytes(), "text/csv"))]


def test_check_on_typed_bills_matches_the_uploaded_register():
    session, business_id = signup("9833300010")
    url = f"/api/businesses/{business_id}/bills"

    with open(SAMPLE / "purchase_register.csv", encoding="utf-8") as file:
        rows = list(csv.DictReader(file))

    saved = 0
    for row in rows:
        day, month, year = row["invoice_date"].split("-")
        response = session.post(url, json={**row, "invoice_date": f"{year}-{month}-{day}", "confirm": True})
        saved += response.status_code == 200

    # The sample's one duplicate bill is refused when typed in.
    assert saved == len(rows) - 1

    # ST-0982 is an older bill (February) still waiting for its supplier.
    months = ",".join(m["month"] for m in session.get(url).json()["months"])
    assert months == "2026-09,2026-02"

    from_bills = session.post(
        "/api/check", files=gstr2b_files(), data={"business_id": business_id, "bill_months": months}
    )
    assert from_bills.status_code == 200, from_bills.text

    uploaded_session, uploaded_business = signup("9833300011")
    from_upload = uploaded_session.post(
        "/api/check", files=register_file() + gstr2b_files(), data={"business_id": uploaded_business}
    )

    assert from_bills.json()["total_at_risk"] == from_upload.json()["total_at_risk"]


def test_check_can_combine_upload_and_saved_bills():
    session, business_id = signup("9833300012")
    session.post(
        f"/api/businesses/{business_id}/bills",
        json=good_bill(invoice_number="NEW-1", invoice_date="2026-09-29", confirm=True),
    )

    response = session.post(
        "/api/check",
        files=register_file() + gstr2b_files(),
        data={"business_id": business_id, "bill_months": "2026-09"},
    )
    assert response.status_code == 200
    numbers = {r["bill_number"] for r in response.json()["results"]}
    assert "NEW-1" in numbers and "ST-1201" in numbers


def test_check_needs_a_register_or_saved_bills():
    session, business_id = signup("9833300013")
    response = session.post("/api/check", files=gstr2b_files(), data={"business_id": business_id})
    assert response.status_code == 400
    assert "saved bills" in response.json()["detail"]

    empty_month = session.post(
        "/api/check", files=gstr2b_files(), data={"business_id": business_id, "bill_months": "2026-09"}
    )
    assert empty_month.status_code == 400
