"""Load the shop's purchase register (bills it received) from CSV.

Required columns: supplier_gstin, invoice_number, invoice_date,
taxable_value. Optional: supplier_name, igst, cgst, sgst, cess,
invoice_value. Dates may be DD-MM-YYYY, DD/MM/YYYY or YYYY-MM-DD.
"""

import csv
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import List

from .gstin import clean as clean_gstin
from .models import Invoice, money

REQUIRED = ("supplier_gstin", "invoice_number", "invoice_date", "taxable_value")

DATE_FORMATS = ("%d-%m-%Y", "%d/%m/%Y", "%Y-%m-%d", "%d.%m.%Y")

# Rounding allowed when checking taxable value + tax = invoice value.
TOTAL_TOLERANCE = Decimal("1.00")


def parse_date(text: str) -> date:
    text = str(text or "").strip()

    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue

    raise ValueError(f"unrecognised date {text!r}")


@dataclass
class RegisterLoad:
    invoices: List[Invoice] = field(default_factory=list)
    # Rows that could not be read at all.
    errors: List[str] = field(default_factory=list)
    # Rows read, but whose amounts don't add up (likely misread).
    warnings: List[str] = field(default_factory=list)


def load_register(path) -> RegisterLoad:
    result = RegisterLoad()

    with open(path, newline="", encoding="utf-8-sig") as file:
        reader = csv.DictReader(file)
        columns = {name.strip().lower() for name in reader.fieldnames or []}

        missing = [name for name in REQUIRED if name not in columns]

        if missing:
            raise ValueError(f"Purchase register is missing columns: {', '.join(missing)}")

        for line, raw in enumerate(reader, start=2):
            row = {key.strip().lower(): (value or "").strip() for key, value in raw.items() if key}

            if not any(row.values()):
                continue

            try:
                invoice = Invoice(
                    supplier_gstin=clean_gstin(row["supplier_gstin"]),
                    supplier_name=row.get("supplier_name", ""),
                    invoice_number=row["invoice_number"],
                    invoice_date=parse_date(row["invoice_date"]),
                    taxable_value=money(row["taxable_value"]),
                    igst=money(row.get("igst")),
                    cgst=money(row.get("cgst")),
                    sgst=money(row.get("sgst")),
                    cess=money(row.get("cess")),
                    source="register",
                    origin=f"row {line}",
                )
            except (ValueError, InvalidOperation) as error:
                result.errors.append(f"Row {line}: {error}")
                continue

            if not invoice.invoice_number:
                result.errors.append(f"Row {line}: invoice number is empty")
                continue

            invoice_value = row.get("invoice_value")

            if invoice_value:
                expected = invoice.taxable_value + invoice.total_tax

                if abs(money(invoice_value) - expected) > TOTAL_TOLERANCE:
                    result.warnings.append(
                        f"Row {line} ({invoice.invoice_number}): taxable value + tax "
                        f"= {expected} but invoice value is {money(invoice_value)}; "
                        "check the bill"
                    )

            result.invoices.append(invoice)

    return result
