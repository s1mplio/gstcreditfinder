"""Command line: match a purchase register against GSTR-2B files.

    python -m gst_credit_finder --register bills.csv --gstr2b 2b_sep.json 2b_oct.json
"""

import argparse
import sys
from datetime import date

from .gstr2b import load_gstr2b
from .matcher import match
from .register import load_register
from .report import summarize, supplier_reminders, text_summary, write_excel


def build_parser():
    parser = argparse.ArgumentParser(
        prog="gst_credit_finder",
        description="Find GST input tax credit at risk by matching purchase bills against GSTR-2B.",
    )
    parser.add_argument("--register", required=True, help="Purchase register CSV")
    parser.add_argument(
        "--gstr2b",
        required=True,
        nargs="+",
        help="One or more GSTR-2B JSON files (include later months to catch late filings)",
    )
    parser.add_argument("--out", default="gst_credit_report.xlsx", help="Excel report path")
    parser.add_argument("--buyer-name", default="", help="Your business name (for supplier messages)")
    parser.add_argument(
        "--today",
        type=date.fromisoformat,
        default=date.today(),
        help="Date used for deadline warnings (YYYY-MM-DD)",
    )
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)

    register = load_register(args.register)

    for error in register.errors:
        print(f"[register] skipped: {error}", file=sys.stderr)

    for warning in register.warnings:
        print(f"[register] check: {warning}", file=sys.stderr)

    gstr2b_invoices = []
    buyer_gstin = ""
    periods = []

    for path in args.gstr2b:
        loaded = load_gstr2b(path)
        gstr2b_invoices.extend(loaded.invoices)
        buyer_gstin = buyer_gstin or loaded.buyer_gstin
        periods.append(loaded.period)

        for section, count in loaded.skipped.items():
            print(f"[gstr2b] {path}: {count} {section} record(s) not checked yet", file=sys.stderr)

    results = match(register.invoices, gstr2b_invoices)
    summary = summarize(results, args.today)
    reminders = supplier_reminders(results, args.buyer_name or "our business", buyer_gstin)

    period_text = "GSTR-2B " + ", ".join(
        f"{p[:2]}-{p[2:]}" for p in sorted(periods, key=lambda p: (p[2:], p[:2])) if p
    )

    print(text_summary(summary, period_text, args.today))

    write_excel(
        args.out,
        results,
        summary,
        reminders,
        args.buyer_name or "Your business",
        buyer_gstin,
        period_text,
        args.today,
    )

    print(f"\nReport saved: {args.out}")
    return 0
