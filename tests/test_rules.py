from datetime import date

from gst_credit_finder.deadlines import itc_deadline
from gst_credit_finder.gstin import validate, with_check_character
from gst_credit_finder.invoice_numbers import core, normalize


# ---------------- GSTIN ----------------

def test_real_published_gstins_are_valid():
    assert validate("27AAPFU0939F1ZV") == (True, "")
    assert validate("27AAACR5055K1Z7") == (True, "")


def test_misread_character_is_caught():
    valid, reason = validate("27AAPFU0938F1ZV")
    assert not valid and "check digit" in reason


def test_bad_length_and_format():
    assert not validate("27AAPFU0939F1Z")[0]
    assert not validate("2AAAPFU0939F1ZV")[0]


def test_lowercase_and_spaces_are_cleaned():
    assert validate(" 27aapfu0939f1zv ")[0]


def test_generated_gstin_is_valid():
    assert validate(with_check_character("27ABKPG4821M1Z"))[0]


# ---------------- invoice numbers ----------------

def test_normalize():
    assert normalize("inv/26-27/0458") == "INV26270458"


def test_core_strips_year_prefix_and_zeros():
    assert core("PD/26-27/0458") == "458"
    assert core("TI-2026-27-458") == "458"
    assert core("458") == "458"
    assert core("INV/2026-2027/00458") == "458"


def test_core_keeps_distinct_serials_distinct():
    assert core("ST-1215") != core("ST-1251")


def test_core_without_digits():
    assert core("CASH") == ""


# ---------------- deadlines ----------------

def test_deadline_is_30_november_after_financial_year():
    assert itc_deadline(date(2026, 9, 6)) == date(2027, 11, 30)   # FY 2026-27
    assert itc_deadline(date(2026, 2, 18)) == date(2026, 11, 30)  # FY 2025-26
    assert itc_deadline(date(2026, 3, 31)) == date(2026, 11, 30)
    assert itc_deadline(date(2026, 4, 1)) == date(2027, 11, 30)
