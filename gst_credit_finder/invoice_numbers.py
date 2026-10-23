"""Invoice number normalisation.

Suppliers write the same invoice number in many ways:
'INV/26-27/0458', 'inv-0458', '458', 'TI/2026-27/458'.
"""

import re

# Financial-year fragments such as 2026-27, 26-27, 2026-2027, 26/27.
FINANCIAL_YEAR = re.compile(r"(?<!\d)(?:20)?\d{2}\s*[-/]\s*(?:20)?\d{2}(?!\d)")


def normalize(number: str) -> str:
    """Uppercase letters and digits only: 'inv/26-27/0458' -> 'INV26270458'."""
    return re.sub(r"[^0-9A-Z]", "", str(number or "").upper())


def core(number: str) -> str:
    """
    The distinctive serial: the last run of digits, without the
    financial year or leading zeros.

    'INV/26-27/0458' -> '458', 'TI-2026-27-458' -> '458', '458' -> '458'.
    Returns '' when there is no serial number.
    """
    text = FINANCIAL_YEAR.sub(" ", str(number or "").upper())
    runs = re.findall(r"\d+", text)

    if not runs:
        return ""

    return runs[-1].lstrip("0") or "0"
