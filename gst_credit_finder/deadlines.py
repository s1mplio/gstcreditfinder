"""Time limit for claiming input tax credit.

Section 16(4) of the CGST Act: credit for an invoice can be claimed up
to 30 November following the end of the financial year the invoice
belongs to (or the annual return filing date, if earlier). This module
uses 30 November; verify with a CA for special cases.
"""

from datetime import date


def financial_year_end(invoice_date: date) -> date:
    """31 March ending the financial year (April to March) of the date."""
    year = invoice_date.year + 1 if invoice_date.month >= 4 else invoice_date.year
    return date(year, 3, 31)


def itc_deadline(invoice_date: date) -> date:
    return date(financial_year_end(invoice_date).year, 11, 30)


def days_left(invoice_date: date, today: date) -> int:
    return (itc_deadline(invoice_date) - today).days
