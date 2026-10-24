"""Run a complete check. Shared by the command line and the website."""

from dataclasses import dataclass, field
from datetime import date
from typing import Dict, List, Optional

from .gstr2b import Gstr2bLoad
from .matcher import action, label, match, needs_action
from .models import ZERO, MatchResult
from .register import RegisterLoad
from .report import Summary, supplier_reminders, summarize


@dataclass
class CheckOutcome:
    results: List[MatchResult]
    summary: Summary
    reminders: Dict[str, dict]
    buyer_name: str
    buyer_gstin: str
    period_text: str
    today: date
    register_errors: List[str] = field(default_factory=list)
    register_warnings: List[str] = field(default_factory=list)
    skipped: List[str] = field(default_factory=list)
    # Credit GSTR-2B makes available, per return period (for the 3B check).
    itc_2b: List[dict] = field(default_factory=list)


def _itc_by_period(gstr2b_loads: List[Gstr2bLoad]) -> List[dict]:
    """
    Claimable B2B credit in each GSTR-2B, by tax head: what GSTR-3B
    Table 4A(5) can claim for that month. Reverse-charge and 'ITC not
    available' records are left out; credit notes aren't read yet.
    """
    totals = {}

    for load in gstr2b_loads:
        if not load.period:
            continue

        heads = totals.setdefault(load.period, {"igst": ZERO, "cgst": ZERO, "sgst": ZERO, "cess": ZERO})

        for invoice in load.invoices:
            if invoice.itc_available and not invoice.reverse_charge:
                heads["igst"] += invoice.igst
                heads["cgst"] += invoice.cgst
                heads["sgst"] += invoice.sgst
                heads["cess"] += invoice.cess

    return [
        {"period": period, **heads}
        for period, heads in sorted(totals.items(), key=lambda item: (item[0][2:], item[0][:2]))
    ]


def _period_text(periods: List[str]) -> str:
    ordered = sorted({p for p in periods if p}, key=lambda p: (p[2:], p[:2]))
    return "GSTR-2B " + ", ".join(f"{p[:2]}-{p[2:]}" for p in ordered)


def run_check(
    register: RegisterLoad,
    gstr2b_loads: List[Gstr2bLoad],
    buyer_name: str = "",
    today: Optional[date] = None,
) -> CheckOutcome:
    today = today or date.today()
    buyer_name = buyer_name.strip() or "Your business"

    invoices = [invoice for load in gstr2b_loads for invoice in load.invoices]
    buyer_gstin = next((load.buyer_gstin for load in gstr2b_loads if load.buyer_gstin), "")

    results = match(register.invoices, invoices)

    skipped = [
        f"{count} {section.upper()} record(s) in GSTR-2B {load.period[:2]}-{load.period[2:]} "
        "not checked yet"
        for load in gstr2b_loads
        for section, count in load.skipped.items()
    ]

    return CheckOutcome(
        results=results,
        summary=summarize(results, today),
        reminders=supplier_reminders(results, buyer_name, buyer_gstin),
        buyer_name=buyer_name,
        buyer_gstin=buyer_gstin,
        period_text=_period_text([load.period for load in gstr2b_loads]),
        today=today,
        register_errors=register.errors,
        register_warnings=register.warnings,
        skipped=skipped,
        itc_2b=_itc_by_period(gstr2b_loads),
    )


# =========================================================
# JSON FOR THE WEBSITE
# =========================================================

def _money(value) -> Optional[float]:
    return float(value) if value is not None else None


def result_to_dict(result: MatchResult, today: date) -> dict:
    bill, record = result.register, result.gstr2b
    invoice = result.invoice

    return {
        "status": result.status,
        "label": label(result.status),
        "action": action(result.status),
        "needs_action": needs_action(result.status),
        "supplier_name": invoice.supplier_name,
        "supplier_gstin": invoice.supplier_gstin,
        "bill_number": bill.invoice_number if bill else None,
        "gstr2b_number": record.invoice_number if record else None,
        "invoice_date": invoice.invoice_date.isoformat(),
        "bill_taxable": _money(bill.taxable_value) if bill else None,
        "bill_gst": _money(bill.total_tax) if bill else None,
        "gstr2b_taxable": _money(record.taxable_value) if record else None,
        "gstr2b_gst": _money(record.total_tax) if record else None,
        "gstr2b_period": record.period if record else None,
        "at_risk": _money(result.at_risk),
        "deadline": result.deadline.isoformat() if result.deadline else None,
        "days_left": (result.deadline - today).days if result.deadline else None,
        "notes": result.notes,
    }


def outcome_to_dict(outcome: CheckOutcome) -> dict:
    summary = outcome.summary

    return {
        "buyer_name": outcome.buyer_name,
        "buyer_gstin": outcome.buyer_gstin,
        "period_text": outcome.period_text,
        "today": outcome.today.isoformat(),
        "total_at_risk": _money(summary.total_at_risk),
        "claimable": _money(summary.claimable),
        "counts": [
            {
                "status": status,
                "label": label(status),
                "count": count,
                "at_risk": _money(summary.at_risk.get(status)),
                "needs_action": needs_action(status),
            }
            for status, count in summary.counts.items()
        ],
        "urgent": [result_to_dict(r, outcome.today) for r in summary.urgent],
        "results": [result_to_dict(r, outcome.today) for r in outcome.results],
        "reminders": [
            {
                "supplier_gstin": gstin,
                "supplier_name": info["supplier_name"],
                "invoices": info["invoices"],
                "at_risk": _money(info["at_risk"]),
                "message": info["message"],
            }
            for gstin, info in outcome.reminders.items()
        ],
        "register_errors": outcome.register_errors,
        "register_warnings": outcome.register_warnings,
        "skipped": outcome.skipped,
        "itc_2b": [
            {"period": row["period"], **{head: _money(row[head]) for head in ("igst", "cgst", "sgst", "cess")}}
            for row in outcome.itc_2b
        ],
    }
