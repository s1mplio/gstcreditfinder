"""Generate fictional test data: a purchase register and two GSTR-2B files.

All businesses, PANs and GSTINs are fictional (GSTINs carry a valid
check digit). The data includes every situation the matcher handles.
The tests run this automatically; to get files for trying the app by
hand, run it with a folder:

    python tests/fixtures/generate_sample.py <folder>
"""

import csv
import json
import random
import sys
from datetime import date
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from gst_credit_finder.gstin import with_check_character  # noqa: E402

OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent / "data"
OUT.mkdir(parents=True, exist_ok=True)
random.seed(2026)

BUYER_NAME = "Shree Ganesh Kirana Stores"
BUYER_GSTIN = with_check_character("27ABKPG4821M1Z")

SUPPLIERS = {
    "sharma": ("Sharma Traders", with_check_character("27AFSPS7316K1Z")),
    "patil": ("Patil Distributors", with_check_character("27AAJFP2954H1Z")),
    "gujarat": ("Gujarat FMCG Wholesale", with_check_character("24AACFG6187Q1Z")),
    "deshmukh": ("Deshmukh Oil Mills", with_check_character("27AAHFD4472B1Z")),
    "sai": ("Sai Packaging Works", with_check_character("27BQWPS5531N1Z")),
    "mahalaxmi": ("Mahalaxmi Dairy Products", with_check_character("27AAKCM8820E1Z")),
    "kulkarni": ("Kulkarni Stationers", with_check_character("27ACXPK3365F1Z")),
    "omsai": ("Om Sai Agencies", with_check_character("27AANFO9142R1Z")),
}


def misread(gstin: str) -> str:
    """The same GSTIN with one PAN digit misread (e.g. 9 read as 8)."""
    position = 7
    wrong = "8" if gstin[position] != "8" else "3"
    return gstin[:position] + wrong + gstin[position + 1:]


def tax_split(taxable: Decimal, rate: int, interstate: bool):
    tax = (taxable * rate / 100).quantize(Decimal("0.01"))

    if interstate:
        return {"igst": tax, "cgst": Decimal("0"), "sgst": Decimal("0")}

    half = (tax / 2).quantize(Decimal("0.01"))
    return {"igst": Decimal("0"), "cgst": half, "sgst": tax - half}


def make_invoice(key, number, day, rate=None, taxable=None):
    name, gstin = SUPPLIERS[key]
    rate = rate or random.choice([5, 12, 18])
    taxable = taxable or Decimal(random.randrange(4000, 60000, 50))
    interstate = gstin[:2] != BUYER_GSTIN[:2]

    return {
        "key": key,
        "name": name,
        "gstin": gstin,
        "number": number,
        "date": day,
        "taxable": taxable,
        "rate": rate,
        **tax_split(taxable, rate, interstate),
    }


# ---------------------------------------------------------
# Bills the shop received (purchase register)
# ---------------------------------------------------------

sep = lambda d: date(2026, 9, d)  # noqa: E731

bills = [
    # Sharma Traders: 3 of 6 never filed by the supplier
    *[make_invoice("sharma", f"ST-{n}", sep(d)) for n, d in [(1201, 2), (1215, 6), (1233, 11), (1248, 16), (1260, 21), (1277, 27)]],
    # Patil: invoice number written differently; one amount wrong in GSTR-2B
    *[make_invoice("patil", f"PD/26-27/{n:04d}", sep(d)) for n, d in [(458, 3), (471, 9), (486, 14), (502, 19), (517, 25)]],
    # Gujarat (interstate, IGST)
    *[make_invoice("gujarat", f"GFW{n}", sep(d), rate=18) for n, d in [(30451, 5), (30488, 12), (30532, 18), (30577, 26)]],
    # Deshmukh: one filed late, appears in October GSTR-2B
    *[make_invoice("deshmukh", f"DOM/{n}", sep(d), rate=5) for n, d in [(881, 4), (894, 10), (907, 17), (921, 29)]],
    # Sai Packaging: GSTR-2B has a typo in one invoice number
    *[make_invoice("sai", f"SP-{n}", sep(d), rate=18) for n, d in [(1029, 7), (1044, 15), (1061, 23)]],
    # Mahalaxmi: all fine, but one bill entered twice and one credit unavailable
    *[make_invoice("mahalaxmi", f"MDP{n}", sep(d), rate=5) for n, d in [(7781, 1), (7805, 8), (7836, 15), (7862, 22)]],
    # Kulkarni: fine
    *[make_invoice("kulkarni", f"KS{n}", sep(d), rate=12) for n, d in [(332, 13), (341, 24)]],
    # Om Sai: GSTIN misread when entering the bill
    make_invoice("omsai", "OSA-2207", sep(20), rate=12),
    # Old bill from last financial year, still missing: deadline 30-11-2026
    make_invoice("sharma", "ST-0982", date(2026, 2, 18), rate=18, taxable=Decimal("42500")),
]

for bill in bills:
    bill["total"] = bill["taxable"] + bill["igst"] + bill["cgst"] + bill["sgst"]

by_number = {bill["number"]: bill for bill in bills}

# ---------------------------------------------------------
# What suppliers reported (GSTR-2B)
# ---------------------------------------------------------

missing = {"ST-1215", "ST-1248", "ST-1277", "ST-0982"}
late = {"DOM/907"}

gstr2b_sep, gstr2b_oct = [], []

for bill in bills:
    if bill["number"] in missing:
        continue

    record = dict(bill)

    if bill["key"] == "patil":
        # Supplier reports just the serial: "PD/26-27/0458" -> "458"
        record["number"] = str(int(bill["number"].split("/")[-1]))

    if bill["number"] == "PD/26-27/0502":
        # Supplier under-reported the amount
        record["taxable"] = bill["taxable"] - Decimal("5000")
        record.update(tax_split(record["taxable"], bill["rate"], False))

    if bill["number"] == "GFW30532":
        # Supplier charged CGST/SGST instead of IGST in its filing
        record.update(tax_split(bill["taxable"], bill["rate"], False))

    if bill["number"] == "SP-1029":
        record["number"] = "SP-1092"

    if bill["number"] == "MDP7836":
        record["itcavl"] = "N"
        record["rsn"] = "P"

    (gstr2b_oct if bill["number"] in late else gstr2b_sep).append(record)

# A sale reported by a supplier with no bill in the register
gstr2b_sep.append(make_invoice("kulkarni", "KS356", sep(28), rate=12))

# ---------------------------------------------------------
# Write purchase register CSV
# ---------------------------------------------------------

rows = list(bills)
rows.insert(25, dict(by_number["MDP7805"]))  # duplicate entry

with open(OUT / "purchase_register.csv", "w", newline="", encoding="utf-8") as file:
    writer = csv.writer(file)
    writer.writerow([
        "supplier_gstin", "supplier_name", "invoice_number", "invoice_date",
        "taxable_value", "igst", "cgst", "sgst", "cess", "invoice_value",
    ])

    for bill in rows:
        gstin = misread(bill["gstin"]) if bill["key"] == "omsai" else bill["gstin"]
        total = bill["total"]

        if bill["number"] == "KS341":
            total += Decimal("900")  # total misread from the bill photo

        writer.writerow([
            gstin, bill["name"], bill["number"], bill["date"].strftime("%d-%m-%Y"),
            bill["taxable"], bill["igst"], bill["cgst"], bill["sgst"], "0", total,
        ])

# ---------------------------------------------------------
# Write GSTR-2B JSON (portal format)
# ---------------------------------------------------------


def gstr2b_json(records, period, generated, filed):
    suppliers = {}

    for record in records:
        supplier = suppliers.setdefault(record["gstin"], {
            "ctin": record["gstin"],
            "trdnm": record["name"].upper(),
            "supfildt": filed,
            "supprd": period,
            "inv": [],
        })

        value = record["taxable"] + record["igst"] + record["cgst"] + record["sgst"]

        supplier["inv"].append({
            "inum": record["number"],
            "typ": "R",
            "dt": record["date"].strftime("%d-%m-%Y"),
            "val": float(value),
            "pos": BUYER_GSTIN[:2],
            "rev": "N",
            "itcavl": record.get("itcavl", "Y"),
            "rsn": record.get("rsn", ""),
            "diffprcnt": 1,
            "srctyp": "",
            "irn": "",
            "irngendate": "",
            "txval": float(record["taxable"]),
            "igst": float(record["igst"]),
            "cgst": float(record["cgst"]),
            "sgst": float(record["sgst"]),
            "cess": 0,
        })

    docdata = {"b2b": list(suppliers.values())}

    if period == "092026":
        # A credit note, to show sections not yet checked
        name, gstin = SUPPLIERS["mahalaxmi"]
        docdata["cdnr"] = [{
            "ctin": gstin, "trdnm": name.upper(), "supfildt": filed, "supprd": period,
            "nt": [{"ntnum": "CN-118", "typ": "C", "dt": "25-09-2026", "val": 525.0,
                    "txval": 500.0, "igst": 0, "cgst": 12.5, "sgst": 12.5, "cess": 0}],
        }]

    return {
        "chksum": "SAMPLE",
        "data": {
            "gstin": BUYER_GSTIN,
            "rtnprd": period,
            "version": "1.0",
            "gendt": generated,
            "docdata": docdata,
        },
    }


for name, records, period, generated, filed in (
    ("gstr2b_092026.json", gstr2b_sep, "092026", "14-10-2026", "11-10-2026"),
    ("gstr2b_102026.json", gstr2b_oct, "102026", "14-11-2026", "11-11-2026"),
):
    with open(OUT / name, "w", encoding="utf-8") as file:
        json.dump(gstr2b_json(records, period, generated, filed), file, indent=2)

print(f"Buyer: {BUYER_NAME} ({BUYER_GSTIN})")
print(f"Purchase register: {len(rows)} bills")
print(f"GSTR-2B Sep: {len(gstr2b_sep)} invoices, Oct: {len(gstr2b_oct)} invoices")
