"""Load GSTR-2B (the government's record of purchases) from the JSON file
downloaded from the GST portal.

Supported: B2B invoices ("b2b") and amended B2B invoices ("b2ba"),
with amounts at invoice level or per item ("items"/"itms").
Credit/debit notes ("cdnr"), imports and ISD are counted but not yet
matched. Field names follow the portal's GSTR-2B JSON; verify against
a real download, since the portal format can change.
"""

import io
import json
import zipfile
from pathlib import Path
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
    return load_gstr2b_bytes(Path(path).read_bytes(), str(path))


def load_gstr2b_upload(content: bytes, filename: str, max_bytes: int, max_files: int) -> List[Gstr2bLoad]:
    """
    An uploaded GSTR-2B: a JSON file, or the ZIP the portal gives for
    large returns. PDF and Excel downloads get a clear error.
    """
    origin = str(filename).replace("\\", "/").split("/")[-1]

    if content.startswith(b"%PDF"):
        raise ValueError(
            f"{origin} is the PDF version of GSTR-2B. On the GST portal, "
            "use 'Download JSON' instead of PDF."
        )

    if not content.startswith(b"PK"):
        return [load_gstr2b_bytes(content, origin)]

    try:
        archive = zipfile.ZipFile(io.BytesIO(content))
        names = archive.namelist()
    except zipfile.BadZipFile:
        raise ValueError(f"{origin} is a damaged ZIP file. Download it again from the GST portal.")

    if any(name.startswith("xl/") for name in names):
        raise ValueError(
            f"{origin} is the Excel version of GSTR-2B. On the GST portal, "
            "use 'Download JSON' instead of Excel."
        )

    members = [info for info in archive.infolist() if info.filename.lower().endswith(".json")]

    if not members:
        raise ValueError(f"{origin} has no GSTR-2B JSON file inside.")

    if len(members) > max_files:
        raise ValueError(f"{origin} has more than {max_files} JSON files.")

    loads = []

    for info in members:
        # Sizes in the ZIP header can lie; read at most max_bytes + 1.
        with archive.open(info) as member:
            data = member.read(max_bytes + 1)

        if len(data) > max_bytes:
            raise ValueError(f"{info.filename} inside {origin} is too large.")

        loads.append(load_gstr2b_bytes(data, f"{origin}/{info.filename}"))

    return loads


def load_gstr2b_bytes(content: bytes, filename: str) -> Gstr2bLoad:
    origin = str(filename).replace("\\", "/").split("/")[-1]

    try:
        raw = json.loads(content.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ValueError(
            f"{origin} is not a GSTR-2B JSON file. Download GSTR-2B from the "
            "GST portal as JSON (not PDF or Excel)."
        )

    if not isinstance(raw, dict):
        raise ValueError(f"{origin} is not a GSTR-2B JSON file.")

    data = raw.get("data", raw)
    docs = data.get("docdata", data)

    if not isinstance(docs, dict) or not any(key in docs for key in ("b2b", "b2ba", "cdnr")):
        raise ValueError(f"{origin} doesn't contain GSTR-2B invoice data (b2b section).")

    result = Gstr2bLoad(
        buyer_gstin=clean_gstin(data.get("gstin")),
        period=str(data.get("rtnprd", "")),
    )

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
