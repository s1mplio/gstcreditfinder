# GST Credit Finder

Finds **GST input tax credit (ITC) a business is losing** by matching its purchase bills against **GSTR-2B**, the government's record of what suppliers reported.

A buyer can only claim credit for invoices that appear in GSTR-2B. When a supplier files late, files the wrong amount, or doesn't file at all, the credit silently disappears. This tool finds each of those bills, puts a rupee value on it, and writes the message to send the supplier.

```text
GST CREDIT CHECK  GSTR-2B 09-2026, 10-2026
====================================================
Credit at risk:       ₹39,741.00
Credit confirmed:     ₹69,444.50

  Matched                       21
  Missing in GSTR-2B             4  (₹29,001.50 at risk)
  Amount mismatch                1  (₹250.00 at risk)
  IGST vs CGST/SGST mismatch     1  (₹8,037.00 at risk)
  Credit not available           1  (₹1,672.50 at risk)
  Invalid GSTIN on bill          1  (₹780.00 at risk)
  Probable match (check)         1
  Duplicate bill                 1
  In GSTR-2B, no bill            2

URGENT: 1 item(s) within 60 days of the claim deadline
  Sharma Traders: ST-0982 (₹7,650.00) deadline 30-11-2026, 38 days left
```

*(Output from the bundled sample data. All businesses and GSTINs in it are fictional.)*

---

## Quick start

```powershell
cd C:\GSTCreditFinder
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt

# Create the demo data, then run the check
python sample_data\generate_sample.py
python -m gst_credit_finder `
    --register sample_data\purchase_register.csv `
    --gstr2b sample_data\gstr2b_092026.json sample_data\gstr2b_102026.json `
    --buyer-name "Shree Ganesh Kirana Stores" `
    --out report.xlsx
```

Open `report.xlsx`. It has four sheets: **Summary**, **Action needed**, **Supplier reminders** (ready-to-send messages) and **All invoices**.

Run the tests:

```powershell
python -m pytest -q
```

---

## Inputs

### 1. Purchase register (CSV)

One row per purchase bill.

| Column | Required | Example |
|---|---|---|
| `supplier_gstin` | yes | `27AFSPS7316K1ZN` |
| `invoice_number` | yes | `ST-1201` |
| `invoice_date` | yes | `02-09-2026` (also `DD/MM/YYYY`, `YYYY-MM-DD`) |
| `taxable_value` | yes | `36700` |
| `igst`, `cgst`, `sgst`, `cess` | no | `0`, `917.50`, `917.50`, `0` |
| `supplier_name` | no | `Sharma Traders` |
| `invoice_value` | no | `38535.00`; used to catch misread totals |

### 2. GSTR-2B (JSON from the GST portal)

GST portal → Returns → GSTR-2B → download JSON. Pass the bill month **and later months**, so invoices that suppliers filed late are found instead of being reported missing.

B2B invoices and amendments (`b2b`, `b2ba`) are matched. Credit notes and other sections are counted and reported as "not checked yet".

---

## How matching works

Matching is plain code with no AI and no guessing: the same inputs always give the same result, and every bill and every GSTR-2B record ends up in exactly one result.

1. **Validate each bill.** GSTINs carry a mod-36 check digit, so a misread or mistyped GSTIN is caught without any lookup. A bill entered twice is flagged.
2. **Match in three passes, strictest first**, so a loose match can never take a record that belongs to an exact one:
   1. **Exact** invoice number (same supplier GSTIN)
   2. **Same serial number** after removing prefixes, financial year and leading zeros (`PD/26-27/0458` = `458`)
   3. **Probable:** different number but same supplier, amounts (±₹1) and date (±3 days). Flagged for a person to confirm, never assumed.
3. **Compare the pair:** taxable value and tax (±₹1), tax type (IGST vs CGST+SGST), and whether GSTR-2B marks the credit available.
4. **Put a value on each problem** and the claim deadline (Section 16(4): 30 November after the financial year ends).

| Result | Meaning | Credit at risk |
|---|---|---|
| Matched | On the government record | none |
| Missing in GSTR-2B | Supplier hasn't reported it | full tax |
| Amount mismatch | Supplier reported different amounts | the shortfall |
| IGST vs CGST/SGST mismatch | Wrong tax type reported | full tax |
| Credit not available | GSTR-2B marks ITC unavailable | full tax |
| Invalid GSTIN on bill | GSTIN misread/mistyped | full tax |
| Probable match (check) | Same bill, different number | confirm |
| Duplicate bill | Bill entered twice | claim once |
| In GSTR-2B, no bill | Supplier reported a sale with no bill | find the bill |

---

## Project layout

```text
gst_credit_finder/
├── gstin.py            # GSTIN format + check-digit validation
├── invoice_numbers.py  # Invoice number normalisation
├── register.py         # Purchase register CSV loader
├── gstr2b.py           # GSTR-2B JSON loader
├── matcher.py          # Three-pass matching and result statuses
├── deadlines.py        # ITC claim deadline (Section 16(4))
├── report.py           # Summary, supplier reminders, Excel report
└── cli.py              # Command line
sample_data/
└── generate_sample.py  # Fictional demo data covering every situation
tests/                  # 28 tests
```

---

## Roadmap

1. **AI bill reading:** photograph or upload supplier bills, extract the register automatically, and flag anything unclear for review.
2. **Web app** for shopkeepers and CA firms: upload, results, one-tap WhatsApp reminders to suppliers.
3. **Multi-client view for CAs:** credit at risk across all clients.
4. **More GSTR-2B sections:** credit/debit notes, imports, ISD.
5. **Invoice Management System (IMS)** accept/reject guidance.

---

## Important

- GST rules and portal file formats change. **Have a Chartered Accountant review the rules** (deadlines, blocked credits, reverse charge, credit notes) before using this for real filings.
- This tool reports on data; it does not file returns or give tax advice.
- Never commit real taxpayer data. `*.xlsx` and `data/` are gitignored.
