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

*(Example output from the test data. All businesses and GSTINs in it are fictional.)*

---

## Quick start: website

```powershell
cd C:\GSTCreditFinder
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt

python run_web.py
```

Open **http://localhost:8080**:

1. **Create an account** with a mobile number or email. Pick **Shopkeeper** (one shop, created at sign-up) or **CA** (add one business per client).
2. Open the business and upload the **purchase register** (CSV or Excel; common column names such as "GSTIN of Supplier" or "Invoice No." are recognised, and title rows above the header are skipped) and one or more **GSTR-2B JSON** files. The ZIP the portal gives for large returns works too, and the upload screen explains how to download GSTR-2B.
   **No billing software?** Under **Purchase bills**, type in each supplier bill as it arrives. GST is calculated from the rate, and typos are flagged before saving: GSTIN check digit, totals, unusual rates, IGST vs CGST/SGST for the supplier's state. Then run the check with "Include bills saved in the app" ticked, with no Excel needed. Saved bills and an uploaded register can be combined.
3. See the **credit at risk**, the problems to fix, and a ready message per supplier with **Send on WhatsApp** / **Copy**. Mark each supplier as *sent*, *replied* or *fixed*.
4. **Download the Excel report** for your CA, now or later from **Past checks**.

Each check is saved to the business. The next month's check shows the **credit recovered**: bills that were at risk before and are now in GSTR-2B. If a business has a GSTIN, uploads of GSTR-2B for a different GSTIN are refused, so a CA can't file one client's data under another.

**What is stored:** uploaded files are processed in memory and never written to disk. Check results and Excel reports are saved in a SQLite database (`data/gst_credit_finder.db`, git-ignored). Passwords are stored as scrypt hashes. Users can delete a check, a business, or their whole account from the site.

| Setting | Default | |
|---|---|---|
| `HOST` | `127.0.0.1` | `0.0.0.0` to open it from other devices |
| `PORT` | `8080` | |
| `GST_DB` | `data/gst_credit_finder.db` | Database file |
| `COOKIE_SECURE` | `0` | Set to `1` when served over HTTPS |
| `RELOAD` | `1` | Set to `0` in production |
| `APP_URL` | | Public address, used as the link in reminder messages |
| `DAILY_JOBS` | `1` | Daily backup + monthly reminders inside the website; `0` if cron runs them |
| `BACKUP_DIR` | `data/backups` | |
| `BACKUP_KEEP` | `14` | Number of daily backups kept |

### Messages: password reset codes and monthly reminders

A login that's an email gets email, and a mobile number gets SMS. **With no provider set up, messages are printed in the server window**, so you can try password reset locally: the 6-digit code appears in the terminal.

| Email (SMTP) | SMS (MSG91) |
|---|---|
| `SMTP_HOST`, `SMTP_PORT` (587), `SMTP_USER`, `SMTP_PASSWORD`, `MAIL_FROM` | `MSG91_AUTH_KEY`, `MSG91_OTP_TEMPLATE`, `MSG91_NUDGE_TEMPLATE` |

Indian SMS needs DLT-registered templates. Register an OTP template with the variable `otp`, and a reminder template with `month`, `count` and `link`, then put their MSG91 template IDs in the settings above. Check the request format against MSG91's current Flow API docs before going live.

**Monthly reminders:** GSTR-2B for a month is ready on the 14th of the next month. From then on, anyone with a business not yet checked for that month gets one message. Users can switch this off in Account settings. To send by hand: `python -m gst_credit_finder.nudges`.

### Backups

The website copies the database to `BACKUP_DIR` once a day and keeps the newest `BACKUP_KEEP` copies. To back up by hand: `python -m gst_credit_finder.backup`. A backup on the same disk won't survive that disk failing, so also copy `BACKUP_DIR` off the server regularly (for example to cloud storage).

### GSTR-3B check

On the results page, enter the ITC claimed in GSTR-3B Table 4A(5). The app compares it per tax head with the credit available in GSTR-2B for that month. Reverse-charge records and records marked "ITC not available" are excluded, and credit notes, imports and ISD aren't counted yet.

### Privacy policy and terms

`/privacy` and `/terms` are **templates**. Replace every `[bracketed]` item with your business details and have a lawyer review them before launch.

## Deploy to Vercel

On Vercel the disk doesn't persist, so the app stores everything in **Postgres** whenever `DATABASE_URL` is set. Locally it keeps using the SQLite file.

1. **Push the code to GitHub.**
2. **Create the project:** on [vercel.com/new](https://vercel.com/new), import the repository. No build settings are needed: `vercel.json` routes every request to `api/index.py`.
3. **Add a database:** in the Vercel project, open **Storage → Create Database → Neon (Postgres)** and connect it to the project. This sets `DATABASE_URL` for you. The tables are created on the first request.
4. **Set environment variables** (Project → Settings → Environment Variables):

   | Variable | Value |
   |---|---|
   | `CRON_SECRET` | A long random string. Vercel Cron sends it to `/api/cron/daily`, which runs the monthly GSTR-2B reminders every day at 09:00 IST |
   | `APP_URL` | Your site's address, e.g. `https://gst-credit-finder.vercel.app` (used in reminder messages) |
   | `SMTP_*` / `MSG91_*` | Optional: email/SMS for reset codes and reminders (see below). Without them, codes only appear in Vercel's function logs |

5. **Redeploy** so the variables take effect.

What changes on Vercel (set automatically from the `VERCEL` variable):

- **Uploads are limited to 4 MB per check** (Vercel refuses requests over 4.5 MB). The page warns before sending. Override with `MAX_UPLOAD_MB`.
- **Cookies are HTTPS-only**, and the daily jobs run through Vercel Cron instead of a background thread.
- **Backups** come from the database provider (Neon keeps point-in-time history), not `backup.py`.

To test against Postgres locally:

```powershell
docker run -d --rm --name gcf-test-pg -e POSTGRES_PASSWORD=test -p 5433:5432 postgres:16-alpine
$env:TEST_DATABASE_URL = "postgresql://postgres:test@localhost:5433/postgres"
python -m pytest -q
```

## Quick start: command line

```powershell
python -m gst_credit_finder `
    --register purchase_register.xlsx `
    --gstr2b gstr2b_092026.json gstr2b_102026.json `
    --buyer-name "Shree Ganesh Kirana Stores" `
    --out report.xlsx
```

Open `report.xlsx`. It has four sheets: **Summary**, **Action needed**, **Supplier reminders** (ready-to-send messages) and **All invoices**.

**Fictional files for trying it out:** `python tests\fixtures\generate_sample.py my_test_files` writes a purchase register and two GSTR-2B files (every business and GSTIN in them is made up) into `my_test_files\`. Upload them on the website or pass them to the command above.

Run the tests (they generate the same files automatically):

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
├── accounts.py         # Users, businesses, saved checks, password reset (SQLite)
├── bills.py            # Purchase bills typed into the app
├── notify.py           # Email / SMS sending
├── nudges.py           # Monthly "GSTR-2B is ready" reminders
├── backup.py           # Daily database backup
├── gstin.py            # GSTIN format + check-digit validation
├── invoice_numbers.py  # Invoice number normalisation
├── register.py         # Purchase register CSV loader
├── gstr2b.py           # GSTR-2B JSON loader
├── matcher.py          # Three-pass matching and result statuses
├── deadlines.py        # ITC claim deadline (Section 16(4))
├── report.py           # Summary, supplier reminders, Excel report
├── service.py          # One check, shared by website and command line
├── web.py              # Website (FastAPI)
├── static/             # Web pages: index.html, app.js, style.css, privacy, terms
└── cli.py              # Command line
tests/                  # 87 tests
└── fixtures/
    └── generate_sample.py  # Fictional test data covering every situation
run_web.py              # Starts the website
```

---

## Roadmap

1. **Bill photos:** read a photographed supplier bill into the bill form (Claude vision), with the existing checks flagging anything unclear.
2. **Import presets** for Tally, Vyapar and Busy exports.
3. **More GSTR-2B sections:** credit/debit notes, imports, ISD.
4. **Invoice Management System (IMS)** accept/reject guidance.
5. **Hindi and Marathi**, supplier report cards, the 180-day payment rule.

---

## Important

- GST rules and portal file formats change. **Have a Chartered Accountant review the rules** (deadlines, blocked credits, reverse charge, credit notes) before using this for real filings.
- This tool reports on data; it does not file returns or give tax advice.
- Never commit real taxpayer data. `*.xlsx` and `data/` are gitignored.
