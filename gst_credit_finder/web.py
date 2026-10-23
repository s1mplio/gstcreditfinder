"""Website: upload bills + GSTR-2B, see credit at risk, download the report.

Uploaded files are processed in memory and never written to disk.
Excel reports are kept in memory briefly so they can be downloaded.
"""

import io
import threading
import time
import uuid
from pathlib import Path
from typing import List

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles

from . import __version__
from .gstr2b import load_gstr2b, load_gstr2b_bytes
from .register import load_register, load_register_bytes
from .report import write_excel
from .service import CheckOutcome, outcome_to_dict, run_check

ROOT = Path(__file__).resolve().parents[1]
STATIC = Path(__file__).resolve().parent / "static"
SAMPLE = ROOT / "sample_data"

MAX_FILE_MB = 15
MAX_GSTR2B_FILES = 13

# Downloadable reports: id -> (created, bytes)
REPORT_TTL_SECONDS = 60 * 60
MAX_REPORTS = 200

TEMPLATE_CSV = (
    "supplier_gstin,supplier_name,invoice_number,invoice_date,"
    "taxable_value,igst,cgst,sgst,cess,invoice_value\n"
    "27AFSPS7316K1ZN,Sharma Traders,ST-1201,02-09-2026,36700,0,917.50,917.50,0,38535.00\n"
)

app = FastAPI(title="GST Credit Finder", version=__version__)
app.mount("/static", StaticFiles(directory=STATIC), name="static")

_reports = {}
_reports_lock = threading.Lock()


def _store_report(outcome: CheckOutcome) -> str:
    buffer = io.BytesIO()

    write_excel(
        buffer,
        outcome.results,
        outcome.summary,
        outcome.reminders,
        outcome.buyer_name,
        outcome.buyer_gstin,
        outcome.period_text,
        outcome.today,
    )

    report_id = uuid.uuid4().hex
    now = time.time()

    with _reports_lock:
        for key in [k for k, (created, _) in _reports.items() if now - created > REPORT_TTL_SECONDS]:
            del _reports[key]

        while len(_reports) >= MAX_REPORTS:
            del _reports[next(iter(_reports))]

        _reports[report_id] = (now, buffer.getvalue())

    return report_id


def _respond(outcome: CheckOutcome) -> dict:
    data = outcome_to_dict(outcome)
    data["report_id"] = _store_report(outcome)
    return data


def _read(upload: UploadFile) -> bytes:
    data = upload.file.read(MAX_FILE_MB * 1024 * 1024 + 1)

    if len(data) > MAX_FILE_MB * 1024 * 1024:
        raise HTTPException(413, f"{upload.filename} is larger than {MAX_FILE_MB} MB.")

    if not data:
        raise HTTPException(400, f"{upload.filename} is empty.")

    return data


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/api/health")
def health():
    return {"ok": True, "version": __version__}


@app.post("/api/check")
def check(
    register_file: UploadFile = File(
        ...,
        alias="register",
        description="Purchase register (CSV or Excel)",
    ),
    gstr2b: List[UploadFile] = File(..., description="GSTR-2B JSON file(s)"),
    buyer_name: str = Form(""),
):
    if len(gstr2b) > MAX_GSTR2B_FILES:
        raise HTTPException(400, f"Upload at most {MAX_GSTR2B_FILES} GSTR-2B files.")

    try:
        register_load = load_register_bytes(
            _read(register_file),
            register_file.filename or "register.csv",
        )
        gstr2b_loads = [load_gstr2b_bytes(_read(f), f.filename or "gstr2b.json") for f in gstr2b]
    except ValueError as error:
        raise HTTPException(400, str(error))

    if not register_load.invoices:
        raise HTTPException(400, "No bills could be read from the purchase register.")

    return _respond(run_check(register_load, gstr2b_loads, buyer_name[:120]))


@app.get("/api/demo")
def demo():
    """Run the check on the bundled fictional sample data."""
    register_path = SAMPLE / "purchase_register.csv"

    if not register_path.exists():
        raise HTTPException(500, "Sample data missing; run sample_data/generate_sample.py")

    outcome = run_check(
        load_register(register_path),
        [load_gstr2b(path) for path in sorted(SAMPLE.glob("gstr2b_*.json"))],
        "Shree Ganesh Kirana Stores (demo)",
    )

    return _respond(outcome)


@app.get("/api/report/{report_id}")
def report(report_id: str):
    with _reports_lock:
        entry = _reports.get(report_id)

    if entry is None:
        raise HTTPException(404, "Report expired. Run the check again to download it.")

    return Response(
        content=entry[1],
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": 'attachment; filename="gst_credit_report.xlsx"'},
    )


@app.get("/api/template")
def template():
    return Response(
        content=TEMPLATE_CSV,
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="purchase_register_template.csv"'},
    )
