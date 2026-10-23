"""Load GSTR-2B (the government's record of purchases) from the JSON file
downloaded from the GST portal.

Supported: B2B invoices ("b2b") and amended B2B invoices ("b2ba"),
with amounts at invoice level or per item ("items"/"itms").
Credit/debit notes ("cdnr"), imports and ISD are counted but not yet
matched. Field names follow the portal's GSTR-2B JSON; verify against
a real download, since the portal format can change.
"""

import json
from dataclasses import dataclass, field
from typing import Dict, List

from .gstin import clean as clean_gstin
from .models import ZERO, Invoice, money
from .register import parse_date

UNSUPPORTED_SECTIONS = ("cdnr", "cdnra", "isd", "isda", "impg", "impgsez")

# Item-level amount fields: invoice-level name -> alternative name.
ITEM_FIELDS = {
    "txval": "txval",
    "igst": "iamt",
    "cgst": "camt",
    "sgst": "samt",
    "cess": "csamt",
}


@dataclass
class Gstr2bLoad:
    buyer_gstin: str = ""
    period: str = ""
    invoices: List[Invoice] = field(default_factory=list)
    # Records in sections not matched yet, e.g. {"cdnr": 3}.
    skipped: Dict[str, int] = field(default_factory=dict)


def _amounts(record: dict) -> dict:
    """Invoice totals, summed from items when given per item."""
    items = record.get("items") or record.get("itms")

    if not items:
        return {key: money(record.get(key)) for key in ("txval", "igst", "cgst", "sgst", "cess")}

    totals = {key: ZERO for key in ITEM_FIELDS}

    for item in items:
        detail = item.get("itm_det", item)

        for key, alternative in ITEM_FIELDS.items():
            totals[key] += money(detail.get(key) or detail.get(alternative))

    return totals


def _invoice(supplier: dict, record: dict, period: str, origin: str) -> Invoice:
    amounts = _amounts(record)

    return Invoice(
        supplier_gstin=clean_gstin(supplier.get("ctin")),
        supplier_name=supplier.get("trdnm", ""),
        invoice_number=str(record.get("inum", "")).strip(),
        invoice_date=parse_date(record.get("dt") or record.get("idt")),
        taxable_value=amounts["txval"],
        igst=amounts["igst"],
        cgst=amounts["cgst"],
        sgst=amounts["sgst"],
        cess=amounts["cess"],
        source="gstr2b",
        period=period,
        itc_available=str(record.get("itcavl", "Y")).upper() != "N",
        reverse_charge=str(record.get("rev", "N")).upper() == "Y",
        origin=origin,
    )


def load_gstr2b(path) -> Gstr2bLoad:
    with open(path, encoding="utf-8-sig") as file:
        raw = json.load(file)

    data = raw.get("data", raw)
    docs = data.get("docdata", data)

    result = Gstr2bLoad(
        buyer_gstin=clean_gstin(data.get("gstin")),
        period=str(data.get("rtnprd", "")),
    )

    origin = str(path).replace("\\", "/").split("/")[-1]

    invoices = {}

    for supplier in docs.get("b2b") or []:
        for record in supplier.get("inv") or []:
            invoice = _invoice(supplier, record, result.period, origin)
            invoices[(invoice.supplier_gstin, invoice.invoice_number.upper())] = invoice

    # Amendments replace the original invoice they correct.
    for supplier in docs.get("b2ba") or []:
        for record in supplier.get("inv") or []:
            original = str(record.get("oinum", "")).strip().upper()
            invoices.pop((clean_gstin(supplier.get("ctin")), original), None)

            invoice = _invoice(supplier, record, result.period, origin)
            invoices[(invoice.supplier_gstin, invoice.invoice_number.upper())] = invoice

    result.invoices = list(invoices.values())

    for section in UNSUPPORTED_SECTIONS:
        count = sum(
            len(supplier.get("nt") or supplier.get("inv") or [])
            for supplier in docs.get(section) or []
        )

        if count:
            result.skipped[section] = count

    return result
