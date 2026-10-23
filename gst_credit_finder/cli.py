"""Command line: match a purchase register against GSTR-2B files.

    python -m gst_credit_finder --register bills.csv --gstr2b 2b_sep.json 2b_oct.json
"""

import argparse
import sys
from datetime import date

from .gstr2b import load_gstr2b
from .register import load_register
from .report import text_summary, write_excel
from .service import run_check


def build_parser():
    parser = argparse.ArgumentParser(
        prog="gst_credit_finder",
        description="Find GST input tax credit at risk by matching purchase bills against GSTR-2B.",
    )
    parser.add_argument("--register", required=True, help="Purchase register (CSV or Excel)")
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
    outcome = run_check(
        register,
        [load_gstr2b(path) for path in args.gstr2b],
        args.buyer_name,
        args.today,
    )

    for error in outcome.register_errors:
        print(f"[register] skipped: {error}", file=sys.stderr)

    for warning in outcome.register_warnings:
        print(f"[register] check: {warning}", file=sys.stderr)

    for note in outcome.skipped:
        print(f"[gstr2b] {note}", file=sys.stderr)

    print(text_summary(outcome.summary, outcome.period_text, outcome.today))

    write_excel(
        args.out,
        outcome.results,
        outcome.summary,
        outcome.reminders,
        outcome.buyer_name,
        outcome.buyer_gstin,
        outcome.period_text,
        outcome.today,
    )

    print(f"\nReport saved: {args.out}")
    return 0
