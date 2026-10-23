"""Match the purchase register against GSTR-2B.

Every bill and every GSTR-2B record ends in exactly one result.
Matching is deterministic: identical inputs always give identical
results, and nothing is guessed. Uncertain pairs are flagged for review.
"""

from collections import defaultdict
from datetime import date
from decimal import Decimal
from typing import Dict, List, Optional

from . import gstin as gstin_module
from .deadlines import itc_deadline
from .invoice_numbers import core, normalize
from .models import ZERO, Invoice, MatchResult

DEFAULT_TOLERANCE = Decimal("1.00")

# How far apart dates may be for an amount-only (probable) match.
PROBABLE_DATE_WINDOW_DAYS = 3

# status -> (label, what to do, needs action)
STATUSES = {
    "matched": ("Matched", "Claim the credit", False),
    "missing_in_2b": (
        "Missing in GSTR-2B",
        "Ask the supplier to file / report this invoice in GSTR-1",
        True,
    ),
    "amount_mismatch": (
        "Amount mismatch",
        "Ask the supplier to amend the amounts they reported",
        True,
    ),
    "tax_type_mismatch": (
        "IGST vs CGST/SGST mismatch",
        "Supplier reported the wrong tax type (place of supply); ask them to amend",
        True,
    ),
    "itc_not_available": (
        "Credit not available",
        "GSTR-2B marks this credit as not available; check the reason with your CA",
        True,
    ),
    "invalid_gstin": (
        "Invalid GSTIN on bill",
        "Check the GSTIN on the bill; it was misread or mistyped",
        True,
    ),
    "probable_match": (
        "Probable match (check)",
        "Same supplier, amounts and date but a different invoice number; confirm it is the same bill",
        True,
    ),
    "duplicate_bill": (
        "Duplicate bill",
        "This bill appears twice in your register; claim it only once",
        True,
    ),
    "only_in_2b": (
        "In GSTR-2B, no bill",
        "Supplier reported a sale with no bill in your register; find the bill or query the supplier",
        True,
    ),
}


def label(status: str) -> str:
    return STATUSES[status][0]


def action(status: str) -> str:
    return STATUSES[status][1]


def needs_action(status: str) -> bool:
    return STATUSES[status][2]


def _period_is_later(period: str, invoice_date: date) -> bool:
    if len(period) != 6 or not period.isdigit():
        return False

    month, year = int(period[:2]), int(period[2:])
    return (year, month) > (invoice_date.year, invoice_date.month)


def _amounts_close(a: Invoice, b: Invoice, tolerance: Decimal) -> bool:
    return (
        abs(a.taxable_value - b.taxable_value) <= tolerance
        and abs(a.total_tax - b.total_tax) <= tolerance
    )


class _Pool:
    """Unused GSTR-2B records, indexed by supplier GSTIN."""

    def __init__(self, invoices: List[Invoice]):
        self.by_gstin: Dict[str, List[Invoice]] = defaultdict(list)

        for invoice in invoices:
            self.by_gstin[invoice.supplier_gstin].append(invoice)

        self.used = set()

    def candidates(self, supplier_gstin: str) -> List[Invoice]:
        return [
            invoice for invoice in self.by_gstin.get(supplier_gstin, [])
            if id(invoice) not in self.used
        ]

    def take(self, invoice: Invoice) -> Invoice:
        self.used.add(id(invoice))
        return invoice

    def remaining(self) -> List[Invoice]:
        return [
            invoice
            for invoices in self.by_gstin.values()
            for invoice in invoices
            if id(invoice) not in self.used
        ]


def _find_exact(bill: Invoice, pool: _Pool) -> Optional[Invoice]:
    key = normalize(bill.invoice_number)

    for candidate in pool.candidates(bill.supplier_gstin):
        if normalize(candidate.invoice_number) == key:
            return candidate

    return None


def _find_by_serial(bill: Invoice, pool: _Pool, tolerance: Decimal) -> Optional[Invoice]:
    serial = core(bill.invoice_number)

    if not serial:
        return None

    same_serial = [
        candidate for candidate in pool.candidates(bill.supplier_gstin)
        if core(candidate.invoice_number) == serial
    ]

    if len(same_serial) == 1:
        return same_serial[0]

    # Several invoices share the serial (e.g. different years):
    # only accept one whose amounts also agree.
    agreeing = [c for c in same_serial if _amounts_close(bill, c, tolerance)]

    return agreeing[0] if len(agreeing) == 1 else None


def _find_probable(bill: Invoice, pool: _Pool, tolerance: Decimal) -> Optional[Invoice]:
    matches = [
        candidate for candidate in pool.candidates(bill.supplier_gstin)
        if _amounts_close(bill, candidate, tolerance)
        and abs((candidate.invoice_date - bill.invoice_date).days) <= PROBABLE_DATE_WINDOW_DAYS
    ]

    # Ambiguous when several records look the same.
    return matches[0] if len(matches) == 1 else None


def _compare(bill: Invoice, record: Invoice, tolerance: Decimal, probable: bool) -> MatchResult:
    notes = []

    if normalize(bill.invoice_number) != normalize(record.invoice_number):
        notes.append(
            f"Invoice number written as {bill.invoice_number!r} on the bill and "
            f"{record.invoice_number!r} in GSTR-2B"
        )

    if bill.invoice_date != record.invoice_date:
        notes.append(
            f"Date {bill.invoice_date:%d-%m-%Y} on the bill, "
            f"{record.invoice_date:%d-%m-%Y} in GSTR-2B"
        )

    if _period_is_later(record.period, bill.invoice_date):
        notes.append(
            f"Appeared in GSTR-2B for {record.period[:2]}-{record.period[2:]}, "
            "after the invoice month (supplier filed late)"
        )

    if record.reverse_charge:
        notes.append("Reverse charge: you pay this tax yourself before claiming credit")

    if probable:
        return MatchResult("probable_match", bill, record, notes)

    if (
        bill.total_tax > 0
        and record.total_tax > 0
        and bill.is_interstate != record.is_interstate
    ):
        notes.append(
            "Bill shows "
            + ("IGST" if bill.is_interstate else "CGST+SGST")
            + ", GSTR-2B shows "
            + ("IGST" if record.is_interstate else "CGST+SGST")
        )
        return MatchResult("tax_type_mismatch", bill, record, notes, at_risk=bill.total_tax)

    if not _amounts_close(bill, record, tolerance):
        notes.append(
            f"Taxable value {bill.taxable_value} on the bill, {record.taxable_value} in GSTR-2B; "
            f"tax {bill.total_tax} on the bill, {record.total_tax} in GSTR-2B"
        )
        shortfall = max(ZERO, bill.total_tax - record.total_tax)
        return MatchResult("amount_mismatch", bill, record, notes, at_risk=shortfall)

    if not record.itc_available:
        return MatchResult("itc_not_available", bill, record, notes, at_risk=bill.total_tax)

    return MatchResult("matched", bill, record, notes)


def match(
    register: List[Invoice],
    gstr2b: List[Invoice],
    tolerance: Decimal = DEFAULT_TOLERANCE,
) -> List[MatchResult]:
    results: List[MatchResult] = []

    # ---------------------------------------------------------
    # Bills that cannot be matched: bad GSTIN, duplicates
    # ---------------------------------------------------------

    candidates: List[Invoice] = []
    seen = set()

    for bill in sorted(register, key=lambda inv: (inv.invoice_date, inv.supplier_gstin)):
        valid, reason = gstin_module.validate(bill.supplier_gstin)

        if not valid:
            results.append(
                MatchResult("invalid_gstin", bill, None, [reason], at_risk=bill.total_tax)
            )
            continue

        key = (bill.supplier_gstin, normalize(bill.invoice_number))

        if key in seen:
            results.append(
                MatchResult(
                    "duplicate_bill",
                    bill,
                    None,
                    [f"Same supplier and invoice number as an earlier row ({bill.origin})"],
                )
            )
            continue

        seen.add(key)
        candidates.append(bill)

    # ---------------------------------------------------------
    # Three passes, strictest first, so a loose match can never
    # take a record that belongs to an exact one.
    # ---------------------------------------------------------

    pool = _Pool(gstr2b)
    unmatched = candidates

    for finder, probable in (
        (lambda bill: _find_exact(bill, pool), False),
        (lambda bill: _find_by_serial(bill, pool, tolerance), False),
        (lambda bill: _find_probable(bill, pool, tolerance), True),
    ):
        still_unmatched = []

        for bill in unmatched:
            record = finder(bill)

            if record is None:
                still_unmatched.append(bill)
                continue

            results.append(_compare(bill, pool.take(record), tolerance, probable))

        unmatched = still_unmatched

    for bill in unmatched:
        results.append(
            MatchResult(
                "missing_in_2b",
                bill,
                None,
                ["Not found in the GSTR-2B files provided"],
                at_risk=bill.total_tax,
            )
        )

    for record in pool.remaining():
        results.append(
            MatchResult("only_in_2b", None, record, ["No matching bill in your purchase register"])
        )

    for result in results:
        if result.at_risk > 0 and result.register is not None:
            result.deadline = itc_deadline(result.register.invoice_date)

    order = list(STATUSES)

    results.sort(
        key=lambda r: (
            not needs_action(r.status),
            order.index(r.status),
            r.invoice.supplier_name.lower(),
            r.invoice.invoice_date,
        )
    )

    return results
