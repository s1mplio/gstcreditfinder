"""GSTIN validation.

A GSTIN is 15 characters: 2-digit state code, 10-character PAN,
entity number, a default 'Z', and a check character. The check
character is a mod-36 checksum, so a misread GSTIN (from a bill
photo or a typo) is usually detected without any lookup.
"""

import re

CHARS = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"

PATTERN = re.compile(r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][0-9A-Z]{3}$")


def check_character(first_14: str) -> str:
    total = 0

    for index, char in enumerate(first_14):
        product = CHARS.index(char) * (1 if index % 2 == 0 else 2)
        total += product // 36 + product % 36

    return CHARS[(36 - total % 36) % 36]


def clean(gstin: str) -> str:
    return re.sub(r"\s+", "", str(gstin or "")).upper()


def validate(gstin: str):
    """Return (is_valid, reason)."""
    gstin = clean(gstin)

    if len(gstin) != 15:
        return False, f"GSTIN must have 15 characters (has {len(gstin)})"

    if not PATTERN.match(gstin):
        return False, "GSTIN format is invalid"

    state = int(gstin[:2])

    if not (1 <= state <= 38 or state in (97, 99)):
        return False, f"Unknown state code {gstin[:2]}"

    expected = check_character(gstin[:14])

    if gstin[14] != expected:
        return False, "GSTIN check digit does not match (likely misread or mistyped)"

    return True, ""


def is_valid(gstin: str) -> bool:
    return validate(gstin)[0]


def with_check_character(first_14: str) -> str:
    """Complete a 14-character GSTIN prefix (used to build test data)."""
    first_14 = clean(first_14)
    return first_14 + check_character(first_14)
