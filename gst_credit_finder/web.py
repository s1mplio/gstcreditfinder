"""Website: upload bills + GSTR-2B, see credit at risk, download the report.

Uploaded files are processed in memory and never written to disk.
Check results and Excel reports are saved to the user's account
(see accounts.py).
"""

import io
import logging
import os
import threading
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path
from typing import List, Optional
from urllib.parse import urlsplit

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import __version__
from . import accounts, backup, bills, notify, nudges
from .accounts import AccountError
from .gstr2b import load_gstr2b_upload
from .register import RegisterLoad, load_register_bytes
from .report import write_excel
from .service import CheckOutcome, outcome_to_dict, run_check

STATIC = Path(__file__).resolve().parent / "static"

MAX_FILE_MB = 15
MAX_GSTR2B_FILES = 13


SESSION_COOKIE = "gcf_session"
# Set COOKIE_SECURE=1 when the site is served over HTTPS.
COOKIE_SECURE = os.getenv("COOKIE_SECURE", "0") == "1"

# Failed logins allowed within the window, per account and per IP
# address (higher, since a CA's office may share one IP).
LOGIN_ATTEMPTS_PER_ACCOUNT = 10
LOGIN_ATTEMPTS_PER_IP = 50
LOGIN_WINDOW_SECONDS = 15 * 60

# Password-reset codes requested per IP address within the login window.
RESET_REQUESTS_PER_IP = 10

# Daily backup + monthly GSTR-2B reminders run inside the website
# unless DAILY_JOBS=0 (e.g. when cron runs them instead).
DAILY_JOBS = os.getenv("DAILY_JOBS", "1") == "1"

log = logging.getLogger("gst_credit_finder.web")

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

TEMPLATE_CSV = (
    "supplier_gstin,supplier_name,invoice_number,invoice_date,"
    "taxable_value,igst,cgst,sgst,cess,invoice_value\n"
    "27AFSPS7316K1ZN,Sharma Traders,ST-1201,02-09-2026,36700,0,917.50,917.50,0,38535.00\n"
)

def _daily_jobs():
    last_run = None

    while True:
        if date.today() != last_run:
            last_run = date.today()

            for job in (backup.run_backup, nudges.send_due):
                try:
                    job()
                except Exception:
                    log.exception("Daily job %s failed", job.__name__)

        time.sleep(15 * 60)


@asynccontextmanager
async def lifespan(app):
    if DAILY_JOBS:
        threading.Thread(target=_daily_jobs, name="daily-jobs", daemon=True).start()
    yield


app = FastAPI(title="GST Credit Finder", version=__version__, lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC), name="static")

_failed_logins = defaultdict(deque)
_failed_lock = threading.Lock()


@app.middleware("http")
async def same_origin_writes(request: Request, call_next):
    """Refuse state-changing requests sent from other websites."""
    origin = request.headers.get("origin")

    if request.method not in ("GET", "HEAD", "OPTIONS") and origin:
        if urlsplit(origin).netloc != request.headers.get("host"):
            return JSONResponse({"detail": "Request from another website was blocked."}, status_code=403)

    return await call_next(request)


def _excel(outcome: CheckOutcome) -> bytes:
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

    return buffer.getvalue()


def _read(upload: UploadFile) -> bytes:
    data = upload.file.read(MAX_FILE_MB * 1024 * 1024 + 1)

    if len(data) > MAX_FILE_MB * 1024 * 1024:
        raise HTTPException(413, f"{upload.filename} is larger than {MAX_FILE_MB} MB.")

    if not data:
        raise HTTPException(400, f"{upload.filename} is empty.")

    return data


# =========================================================
# ACCOUNTS
# =========================================================

def current_user(request: Request) -> dict:
    user = accounts.session_user(request.cookies.get(SESSION_COOKIE, ""))

    if user is None:
        raise HTTPException(401, "Please log in.")

    return user


def _business(user: dict, business_id: int) -> dict:
    business = accounts.get_business(user["id"], business_id)

    if business is None:
        raise HTTPException(404, "Business not found.")

    return business


def _too_many_failures(limits: dict) -> bool:
    cutoff = time.time() - LOGIN_WINDOW_SECONDS

    with _failed_lock:
        for key, limit in limits.items():
            attempts = _failed_logins[key]
            while attempts and attempts[0] < cutoff:
                attempts.popleft()
            if len(attempts) >= limit:
                return True

    return False


def _record_failure(*keys: str):
    with _failed_lock:
        for key in keys:
            _failed_logins[key].append(time.time())


def _me(user_id: int) -> dict:
    return {"user": accounts.get_user(user_id), "businesses": accounts.list_businesses(user_id)}


def _signed_in(user_id: int) -> JSONResponse:
    response = JSONResponse(_me(user_id))
    response.set_cookie(
        SESSION_COOKIE,
        accounts.create_session(user_id),
        max_age=accounts.SESSION_DAYS * 86400,
        httponly=True,
        samesite="lax",
        secure=COOKIE_SECURE,
    )
    return response


def _signed_out() -> JSONResponse:
    response = JSONResponse({"ok": True})
    response.delete_cookie(SESSION_COOKIE)
    return response


class Signup(BaseModel):
    name: str
    login: str
    password: str
    role: str = "shop"
    business_name: str = ""
    business_gstin: str = ""


class Login(BaseModel):
    login: str
    password: str


class NewBusiness(BaseModel):
    name: str
    gstin: str = ""


class ReminderStatus(BaseModel):
    status: str


class ForgotPassword(BaseModel):
    login: str


class ResetPassword(BaseModel):
    login: str
    code: str
    password: str


class Settings(BaseModel):
    notify: bool


@app.exception_handler(AccountError)
def account_error(request: Request, error: AccountError):
    return JSONResponse({"detail": str(error)}, status_code=400)


@app.post("/api/signup")
def signup(body: Signup):
    user_id = accounts.create_user(
        body.name,
        body.login,
        body.password,
        body.role,
        business_name=body.business_name,
        business_gstin=body.business_gstin,
    )
    return _signed_in(user_id)


@app.post("/api/login")
def login(body: Login, request: Request):
    ip = f"ip:{request.client.host if request.client else ''}"
    login_key = f"login:{body.login.strip().lower()}"

    if _too_many_failures({ip: LOGIN_ATTEMPTS_PER_IP, login_key: LOGIN_ATTEMPTS_PER_ACCOUNT}):
        raise HTTPException(429, "Too many wrong attempts. Wait 15 minutes and try again.")

    user_id = accounts.authenticate(body.login, body.password)

    if user_id is None:
        _record_failure(ip, login_key)
        raise HTTPException(401, "Wrong mobile number / email or password.")

    return _signed_in(user_id)


@app.post("/api/logout")
def logout(request: Request):
    accounts.end_session(request.cookies.get(SESSION_COOKIE, ""))
    return _signed_out()


@app.post("/api/password/forgot")
def forgot_password(body: ForgotPassword, request: Request):
    ip = f"reset-ip:{request.client.host if request.client else ''}"

    if _too_many_failures({ip: RESET_REQUESTS_PER_IP}):
        raise HTTPException(429, "Too many requests. Wait 15 minutes and try again.")

    _record_failure(ip)
    started = accounts.start_password_reset(body.login)

    if started:
        login, code = started
        try:
            notify.send(
                login,
                "Your GST Credit Finder password reset code",
                f"Your GST Credit Finder code is {code}. It expires in "
                f"{accounts.RESET_CODE_MINUTES} minutes. Don't share it with anyone.",
                sms_template_env="MSG91_OTP_TEMPLATE",
                sms_vars={"otp": code},
            )
        except notify.NotifyError as error:
            raise HTTPException(503, str(error))

    # Same answer either way, so this can't be used to find accounts.
    return {"ok": True}


@app.post("/api/password/reset")
def reset_password(body: ResetPassword):
    return _signed_in(accounts.finish_password_reset(body.login, body.code, body.password))


@app.put("/api/me/settings")
def update_settings(body: Settings, user: dict = Depends(current_user)):
    accounts.set_notify(user["id"], body.notify)
    return _me(user["id"])


@app.get("/api/me")
def me(user: dict = Depends(current_user)):
    return _me(user["id"])


@app.delete("/api/me")
def delete_me(user: dict = Depends(current_user)):
    accounts.delete_user(user["id"])
    return _signed_out()


@app.post("/api/businesses")
def add_business(body: NewBusiness, user: dict = Depends(current_user)):
    business_id = accounts.create_business(user["id"], body.name, body.gstin)
    return accounts.get_business(user["id"], business_id)


@app.delete("/api/businesses/{business_id}")
def remove_business(business_id: int, user: dict = Depends(current_user)):
    if not accounts.delete_business(user["id"], business_id):
        raise HTTPException(404, "Business not found.")
    return {"ok": True}


@app.get("/api/businesses/{business_id}/checks")
def business_checks(business_id: int, user: dict = Depends(current_user)):
    business = _business(user, business_id)
    return {"business": business, "checks": accounts.list_checks(user["id"], business_id)}


@app.put("/api/businesses/{business_id}/reminders/{supplier_gstin}")
def update_reminder(
    business_id: int,
    supplier_gstin: str,
    body: ReminderStatus,
    user: dict = Depends(current_user),
):
    _business(user, business_id)
    accounts.set_reminder_status(business_id, supplier_gstin, body.status)
    return accounts.reminder_statuses(business_id)


@app.get("/api/checks/{check_id}")
def saved_check(check_id: int, user: dict = Depends(current_user)):
    data = accounts.get_check(user["id"], check_id)

    if data is None:
        raise HTTPException(404, "Check not found.")

    return data


@app.get("/api/checks/{check_id}/report")
def saved_report(check_id: int, user: dict = Depends(current_user)):
    content = accounts.get_report(user["id"], check_id)

    if content is None:
        raise HTTPException(404, "Report not found.")

    return Response(
        content=content,
        media_type=XLSX,
        headers={"Content-Disposition": f'attachment; filename="gst_credit_report_{check_id}.xlsx"'},
    )


@app.delete("/api/checks/{check_id}")
def remove_check(check_id: int, user: dict = Depends(current_user)):
    if not accounts.delete_check(user["id"], check_id):
        raise HTTPException(404, "Check not found.")
    return {"ok": True}


# =========================================================
# BILLS ENTERED IN THE APP
# =========================================================

class NewBill(BaseModel):
    supplier_gstin: str
    supplier_name: str = ""
    invoice_number: str
    invoice_date: str
    taxable_value: str
    igst: str = "0"
    cgst: str = "0"
    sgst: str = "0"
    cess: str = "0"
    invoice_value: str = ""
    confirm: bool = False


@app.get("/api/businesses/{business_id}/bills")
def list_bills(business_id: int, month: str = "", user: dict = Depends(current_user)):
    _business(user, business_id)
    months = bills.months(business_id)
    month = month or (months[0]["month"] if months else date.today().isoformat()[:7])

    return {
        "month": month,
        "months": months,
        "bills": bills.list_month(business_id, month),
        "suppliers": bills.suppliers(business_id),
    }


@app.post("/api/businesses/{business_id}/bills")
def add_bill(business_id: int, body: NewBill, user: dict = Depends(current_user)):
    business = _business(user, business_id)
    data = body.model_dump()
    bill, warnings = bills.add(business, data, confirm=data.pop("confirm"))

    if bill is None:
        return JSONResponse({"saved": False, "warnings": warnings}, status_code=409)

    return {"saved": True, "bill": bill, "warnings": warnings}


@app.delete("/api/businesses/{business_id}/bills/{bill_id}")
def remove_bill(business_id: int, bill_id: int, user: dict = Depends(current_user)):
    _business(user, business_id)
    if not bills.delete(business_id, bill_id):
        raise HTTPException(404, "Bill not found.")
    return {"ok": True}


# =========================================================
# PAGES + CHECKS
# =========================================================

@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/privacy")
def privacy():
    return FileResponse(STATIC / "privacy.html")


@app.get("/terms")
def terms():
    return FileResponse(STATIC / "terms.html")


@app.get("/api/health")
def health():
    return {"ok": True, "version": __version__}


@app.post("/api/check")
def check(
    register_file: Optional[UploadFile] = File(
        None,
        alias="register",
        description="Purchase register (CSV or Excel)",
    ),
    gstr2b: List[UploadFile] = File(..., description="GSTR-2B JSON file(s)"),
    business_id: int = Form(...),
    bill_months: str = Form("", description="Saved bills to include: 'YYYY-MM,YYYY-MM'"),
    user: dict = Depends(current_user),
):
    business = _business(user, business_id)

    if len(gstr2b) > MAX_GSTR2B_FILES:
        raise HTTPException(400, f"Upload at most {MAX_GSTR2B_FILES} GSTR-2B files.")

    months = sorted({m.strip() for m in bill_months.split(",") if m.strip()})[:24]

    if register_file is None and not months:
        raise HTTPException(400, "Upload a purchase register or choose saved bills to check.")

    try:
        register_load = (
            load_register_bytes(_read(register_file), register_file.filename or "register.csv")
            if register_file is not None
            else RegisterLoad()
        )
        register_load.invoices += bills.invoices(business["id"], months)
        gstr2b_loads = [
            load
            for f in gstr2b
            for load in load_gstr2b_upload(
                _read(f), f.filename or "gstr2b.json", MAX_FILE_MB * 1024 * 1024, MAX_GSTR2B_FILES
            )
        ]
    except ValueError as error:
        raise HTTPException(400, str(error))

    if len(gstr2b_loads) > MAX_GSTR2B_FILES:
        raise HTTPException(400, f"Upload at most {MAX_GSTR2B_FILES} months of GSTR-2B at once.")

    if not register_load.invoices:
        raise HTTPException(400, "No bills found: the register is empty and no saved bills were chosen.")

    # Catch a CA uploading one client's GSTR-2B under another client.
    file_gstins = {load.buyer_gstin for load in gstr2b_loads if load.buyer_gstin}

    if business["gstin"] and file_gstins - {business["gstin"]}:
        raise HTTPException(
            400,
            f"These GSTR-2B files are for GSTIN {', '.join(sorted(file_gstins))}, "
            f"but {business['name']} is GSTIN {business['gstin']}. Choose the right business.",
        )

    if not business["gstin"] and len(file_gstins) == 1:
        accounts.set_business_gstin(business["id"], next(iter(file_gstins)))

    outcome = run_check(register_load, gstr2b_loads, business["name"])
    check_id = accounts.save_check(business["id"], outcome_to_dict(outcome), _excel(outcome))

    return accounts.get_check(user["id"], check_id)


@app.get("/api/template")
def template():
    return Response(
        content=TEMPLATE_CSV,
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="purchase_register_template.csv"'},
    )
