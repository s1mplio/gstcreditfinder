from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import List, Optional

ZERO = Decimal("0")


def money(value) -> Decimal:
    """Parse a rupee amount ('1,234.50', 1234.5, '') into a Decimal."""
    if value is None:
        return ZERO

    text = str(value).replace(",", "").replace("₹", "").strip()

    if not text:
        return ZERO

    return Decimal(text).quantize(Decimal("0.01"))


@dataclass
class Invoice:
    """One B2B purchase invoice, from a bill or from GSTR-2B."""

    supplier_gstin: str
    invoice_number: str
    invoice_date: date
    taxable_value: Decimal
    igst: Decimal = ZERO
    cgst: Decimal = ZERO
    sgst: Decimal = ZERO
    cess: Decimal = ZERO
    supplier_name: str = ""

    # "register" (the shop's bills) or "gstr2b" (government record)
    source: str = ""

    # GSTR-2B return period, "MMYYYY"
    period: str = ""

    # GSTR-2B flags
    itc_available: bool = True
    reverse_charge: bool = False

    # Where the row came from, for messages ("row 12", "092026.json")
    origin: str = ""

    @property
    def total_tax(self) -> Decimal:
        return self.igst + self.cgst + self.sgst + self.cess

    @property
    def is_interstate(self) -> bool:
        return self.igst > 0


@dataclass
class MatchResult:
    """Outcome for one bill and/or one GSTR-2B record."""

    status: str
    register: Optional[Invoice] = None
    gstr2b: Optional[Invoice] = None
    notes: List[str] = field(default_factory=list)

    # Tax credit that cannot be claimed until this is fixed.
    at_risk: Decimal = ZERO

    # Last date to claim the credit (Section 16(4)).
    deadline: Optional[date] = None

    @property
    def invoice(self) -> Invoice:
        return self.register or self.gstr2b
