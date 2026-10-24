"""Purchase bills entered in the app, one at a time.

For shops without billing software: bills are typed in (later, read
from a photo) through the month, and a check can use them instead of
an uploaded purchase register.

Typos are the main risk, so a bill is checked before it is saved:
GSTIN check digit, taxable value + tax = total, a standard GST rate,
CGST = SGST, and the right tax type for the supplier's state. These
come back as warnings; the user confirms to save anyway.
"""

import time
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from typing import List, Optional

from . import accounts, db as database
from .accounts import AccountError
from .gstin import clean as clean_gstin
from .gstin import validate as validate_gstin
from .invoice_numbers import normalize
from .models import Invoice, money

GST_RATES = (Decimal("0.25"), Decimal("3"), Decimal("5"), Decimal("12"), Decimal("18"), Decimal("28"))
TOTAL_TOLERANCE = Decimal("1.00")
MAX_BILLS_PER_BUSINESS = 50000

AMOUNTS = ("taxable_value", "igst", "cgst", "sgst", "cess", "invoice_value")


def _amount(value, label: str) -> Decimal:
    try:
        amount = money(value)
    except (InvalidOperation, ValueError):
        raise AccountError(f"{label}: enter an amount in rupees, like 12500.50.")

    if amount < 0:
        raise AccountError(f"{label} can't be negative.")

    return amount


def validate(data: dict, business_gstin: str = "", today: Optional[date] = None):
    """
    Check a bill typed by the user. Returns (clean bill, warnings);
    raises AccountError for anything that can't be saved at all.
    """
    today = today or date.today()

    bill = {
        "supplier_gstin": clean_gstin(data.get("supplier_gstin")),
        "supplier_name": str(data.get("supplier_name") or "").strip()[:120],
        "invoice_number": str(data.get("invoice_number") or "").strip()[:40],
    }

    if not bill["supplier_gstin"]:
        raise AccountError("Enter the supplier's GSTIN (printed on the bill).")
    if not bill["invoice_number"]:
        raise AccountError("Enter the invoice number.")

    try:
        invoice_date = date.fromisoformat(str(data.get("invoice_date") or ""))
    except ValueError:
        raise AccountError("Enter the invoice date.")

    # A day of slack: the server may run on UTC while the shop is in IST.
    if invoice_date > today + timedelta(days=1):
        raise AccountError("The invoice date is in the future.")
    if invoice_date < today - timedelta(days=3 * 366):
        raise AccountError("The invoice date is more than three years ago.")

    bill["invoice_date"] = invoice_date.isoformat()

    labels = {
        "taxable_value": "Taxable value",
        "igst": "IGST",
        "cgst": "CGST",
        "sgst": "SGST",
        "cess": "Cess",
        "invoice_value": "Bill total",
    }
    amounts = {key: _amount(data.get(key), labels[key]) for key in AMOUNTS}

    if amounts["taxable_value"] <= 0:
        raise AccountError("Enter the taxable value (amount before GST).")

    tax = amounts["igst"] + amounts["cgst"] + amounts["sgst"]

    if not amounts["invoice_value"]:
        amounts["invoice_value"] = amounts["taxable_value"] + tax + amounts["cess"]

    bill.update({key: str(value) for key, value in amounts.items()})

    warnings = []

    valid, reason = validate_gstin(bill["supplier_gstin"])
    if not valid:
        warnings.append(f"Supplier GSTIN: {reason}. Check it against the bill.")

    expected = amounts["taxable_value"] + tax + amounts["cess"]
    if abs(expected - amounts["invoice_value"]) > TOTAL_TOLERANCE:
        warnings.append(
            f"Taxable value + GST = ₹{expected}, but the bill total is ₹{amounts['invoice_value']}."
        )

    if tax == 0:
        warnings.append("No GST entered. Bills without GST give no credit.")
    elif amounts["igst"] and (amounts["cgst"] or amounts["sgst"]):
        warnings.append("A bill has either IGST or CGST + SGST, not both.")
    elif abs(amounts["cgst"] - amounts["sgst"]) > Decimal("0.05"):
        warnings.append("CGST and SGST are usually equal. Check both amounts.")
    elif not any(
        abs(tax - amounts["taxable_value"] * rate / 100) <= Decimal("1.00") for rate in GST_RATES
    ):
        # Bills with items at several rates land here too, so it's only a warning.
        percent = tax * 100 / amounts["taxable_value"]
        warnings.append(
            f"GST is {percent:.2f}% of the taxable value, not a standard rate "
            "(5, 12, 18 or 28%). Fine if the bill has items at different rates."
        )

    if valid and business_gstin and tax:
        same_state = bill["supplier_gstin"][:2] == business_gstin[:2]
        if same_state and amounts["igst"]:
            warnings.append("Supplier is in your state, so the bill should have CGST + SGST, not IGST.")
        if not same_state and (amounts["cgst"] or amounts["sgst"]):
            warnings.append("Supplier is in another state, so the bill should have IGST, not CGST + SGST.")

    return bill, warnings


def add(business: dict, data: dict, confirm: bool = False, today: Optional[date] = None):
    """
    Save a bill. Returns (bill, warnings); when there are warnings and
    the user hasn't confirmed, nothing is saved and bill is None.
    """
    bill, warnings = validate(data, business.get("gstin", ""), today)

    if warnings and not confirm:
        return None, warnings

    key = normalize(bill["invoice_number"])

    with accounts.connect() as db:
        count = db.execute(
            "SELECT COUNT(*) AS n FROM bills WHERE business_id = ?", (business["id"],)
        ).fetchone()["n"]
        if count >= MAX_BILLS_PER_BUSINESS:
            raise AccountError("This business has too many saved bills.")

        try:
            bill_id = db.insert(
                "INSERT INTO bills (business_id, supplier_gstin, supplier_name, invoice_number, invoice_key, "
                "invoice_date, taxable_value, igst, cgst, sgst, cess, invoice_value, created) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    business["id"], bill["supplier_gstin"], bill["supplier_name"], bill["invoice_number"], key,
                    bill["invoice_date"], *(bill[k] for k in AMOUNTS), time.time(),
                ),
            )
        except database.IntegrityError:
            raise AccountError(
                f"Bill {bill['invoice_number']} from this supplier is already saved."
            )

    bill["id"] = bill_id
    return bill, warnings


def _public(row) -> dict:
    bill = dict(row)
    for key in AMOUNTS:
        bill[key] = float(bill[key])
    bill["gst"] = round(bill["igst"] + bill["cgst"] + bill["sgst"] + bill["cess"], 2)
    return bill


def months(business_id: int) -> List[dict]:
    """Months that have bills, newest first: {month: 'YYYY-MM', count, gst}."""
    with accounts.connect() as db:
        rows = db.execute(
            "SELECT substr(invoice_date, 1, 7) AS month, COUNT(*) AS count, "
            "SUM(CAST(igst AS NUMERIC) + CAST(cgst AS NUMERIC) + CAST(sgst AS NUMERIC) + CAST(cess AS NUMERIC)) AS gst "
            "FROM bills WHERE business_id = ? GROUP BY month ORDER BY month DESC",
            (business_id,),
        ).fetchall()

    return [{"month": r["month"], "count": r["count"], "gst": round(float(r["gst"]), 2)} for r in rows]


def list_month(business_id: int, month: str) -> List[dict]:
    with accounts.connect() as db:
        rows = db.execute(
            "SELECT id, supplier_gstin, supplier_name, invoice_number, invoice_date, "
            "taxable_value, igst, cgst, sgst, cess, invoice_value FROM bills "
            "WHERE business_id = ? AND substr(invoice_date, 1, 7) = ? "
            "ORDER BY invoice_date DESC, id DESC",
            (business_id, month),
        ).fetchall()

    return [_public(row) for row in rows]


def suppliers(business_id: int) -> List[dict]:
    """Suppliers seen before, to fill in names as the user types a GSTIN."""
    with accounts.connect() as db:
        rows = db.execute(
            "SELECT supplier_gstin, MAX(supplier_name) AS supplier_name FROM bills "
            "WHERE business_id = ? AND supplier_name != '' "
            "GROUP BY supplier_gstin ORDER BY MAX(created) DESC",
            (business_id,),
        ).fetchall()

    return [dict(row) for row in rows]


def delete(business_id: int, bill_id: int) -> bool:
    with accounts.connect() as db:
        cursor = db.execute("DELETE FROM bills WHERE id = ? AND business_id = ?", (bill_id, business_id))
        return cursor.rowcount > 0


def invoices(business_id: int, wanted_months: List[str]) -> List[Invoice]:
    """Saved bills from the given months, ready for matching."""
    if not wanted_months:
        return []

    marks = ",".join("?" for _ in wanted_months)

    with accounts.connect() as db:
        rows = db.execute(
            f"SELECT * FROM bills WHERE business_id = ? AND substr(invoice_date, 1, 7) IN ({marks}) "
            "ORDER BY invoice_date, id",
            (business_id, *wanted_months),
        ).fetchall()

    return [
        Invoice(
            supplier_gstin=row["supplier_gstin"],
            supplier_name=row["supplier_name"],
            invoice_number=row["invoice_number"],
            invoice_date=date.fromisoformat(row["invoice_date"]),
            taxable_value=money(row["taxable_value"]),
            igst=money(row["igst"]),
            cgst=money(row["cgst"]),
            sgst=money(row["sgst"]),
            cess=money(row["cess"]),
            source="register",
            origin="saved bill",
        )
        for row in rows
    ]
