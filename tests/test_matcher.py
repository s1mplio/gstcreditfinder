from datetime import date
from decimal import Decimal

from gst_credit_finder.gstin import with_check_character
from gst_credit_finder.matcher import match
from gst_credit_finder.models import Invoice

SUPPLIER = with_check_character("27AFSPS7316K1Z")
OTHER = with_check_character("27AAJFP2954H1Z")
INTERSTATE = with_check_character("24AACFG6187Q1Z")


def bill(number, day=6, taxable="10000", cgst="900", sgst="900", igst="0", gstin=SUPPLIER, **extra):
    return Invoice(
        supplier_gstin=gstin,
        invoice_number=number,
        invoice_date=date(2026, 9, day),
        taxable_value=Decimal(taxable),
        cgst=Decimal(cgst),
        sgst=Decimal(sgst),
        igst=Decimal(igst),
        source="register",
        **extra,
    )


def record(number, period="092026", **kwargs):
    invoice = bill(number, **kwargs)
    invoice.source = "gstr2b"
    invoice.period = period
    return invoice


def statuses(results):
    return sorted(r.status for r in results)


def test_exact_match():
    results = match([bill("ST-1201")], [record("ST-1201")])
    assert statuses(results) == ["matched"]
    assert results[0].at_risk == 0


def test_missing_in_gstr2b_puts_full_tax_at_risk_with_deadline():
    results = match([bill("ST-1215")], [])
    assert statuses(results) == ["missing_in_2b"]
    assert results[0].at_risk == Decimal("1800")
    assert results[0].deadline == date(2027, 11, 30)


def test_invoice_number_written_differently_still_matches():
    results = match([bill("PD/26-27/0458")], [record("458")])
    assert statuses(results) == ["matched"]
    assert "written as" in results[0].notes[0]


def test_same_serial_different_supplier_does_not_match():
    results = match([bill("458")], [record("458", gstin=OTHER)])
    assert statuses(results) == ["missing_in_2b", "only_in_2b"]


def test_amount_mismatch_risk_is_the_shortfall():
    results = match([bill("A1")], [record("A1", taxable="9000", cgst="810", sgst="810")])
    assert statuses(results) == ["amount_mismatch"]
    assert results[0].at_risk == Decimal("180")


def test_rounding_within_tolerance_matches():
    results = match([bill("A1")], [record("A1", cgst="900.40", sgst="900.40")])
    assert statuses(results) == ["matched"]


def test_tax_type_mismatch():
    results = match(
        [bill("G1", gstin=INTERSTATE, cgst="0", sgst="0", igst="1800")],
        [record("G1", gstin=INTERSTATE)],
    )
    assert statuses(results) == ["tax_type_mismatch"]
    assert results[0].at_risk == Decimal("1800")


def test_credit_not_available():
    results = match([bill("A1")], [record("A1", itc_available=False)])
    assert statuses(results) == ["itc_not_available"]


def test_late_filing_is_matched_with_note():
    results = match([bill("A1")], [record("A1", period="102026")])
    assert statuses(results) == ["matched"]
    assert any("filed late" in note for note in results[0].notes)


def test_typo_in_number_is_probable_not_matched():
    results = match([bill("SP-1029")], [record("SP-1092")])
    assert statuses(results) == ["probable_match"]


def test_probable_match_needs_a_unique_candidate():
    results = match([bill("SP-1029")], [record("X-1"), record("X-2")])
    assert "probable_match" not in statuses(results)


def test_exact_match_wins_over_probable():
    # The probable pass must not steal the record that matches exactly.
    results = match([bill("B-1", day=6), bill("B-2", day=6)], [record("B-2", day=6)])
    by_number = {r.register.invoice_number: r.status for r in results}
    assert by_number == {"B-1": "missing_in_2b", "B-2": "matched"}


def test_invalid_gstin_is_flagged():
    wrong = SUPPLIER[:7] + ("8" if SUPPLIER[7] != "8" else "3") + SUPPLIER[8:]
    results = match([bill("A1", gstin=wrong)], [record("A1")])
    assert statuses(results) == ["invalid_gstin", "only_in_2b"]


def test_duplicate_bill_is_flagged():
    results = match([bill("A1"), bill("a-1")], [record("A1")])
    assert statuses(results) == ["duplicate_bill", "matched"]


def test_record_without_bill():
    results = match([], [record("Z9")])
    assert statuses(results) == ["only_in_2b"]


def test_every_input_appears_exactly_once():
    bills = [bill(f"N-{i}", day=1 + i) for i in range(6)]
    records = [record(f"N-{i}", day=1 + i) for i in range(0, 6, 2)] + [record("EXTRA")]
    results = match(bills, records)

    seen_bills = [r.register for r in results if r.register]
    seen_records = [r.gstr2b for r in results if r.gstr2b]

    assert len(seen_bills) == len(bills) and len({id(b) for b in seen_bills}) == len(bills)
    assert len(seen_records) == len(records) and len({id(r) for r in seen_records}) == len(records)
