"""Accounts, businesses and saved checks (see db.py for storage).

One user owns one or more businesses: a shopkeeper usually has one,
a CA has one per client. Each check run is saved against a business
so the next month can show what was recovered.

Uploaded files are never stored. Only the check results and the
Excel report are kept, and the owner can delete them at any time.
"""

import hashlib
import hmac
import json
import re
import secrets
import time
from typing import List, Optional

from .db import connect
from .gstin import clean as clean_gstin
from .gstin import validate as validate_gstin
from .invoice_numbers import normalize

SESSION_DAYS = 30
MAX_BUSINESSES = 500

REMINDER_STATUSES = ("pending", "sent", "replied", "fixed")
ROLES = ("shop", "ca")

RESET_CODE_MINUTES = 10
RESET_RESEND_SECONDS = 60
RESET_MAX_ATTEMPTS = 5


class AccountError(ValueError):
    """A problem the user can fix (shown to them as-is)."""


# =========================================================
# PASSWORDS + LOGIN IDS
# =========================================================

def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=2 ** 14, r=8, p=1)
    return f"scrypt${salt.hex()}${digest.hex()}"


def check_password(password: str, stored: str) -> bool:
    try:
        _, salt, digest = stored.split("$")
    except ValueError:
        return False

    candidate = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=2 ** 14, r=8, p=1)
    return hmac.compare_digest(candidate.hex(), digest)


def normalize_login(login: str) -> str:
    """A mobile number ('+91 98765-43210' -> '9876543210') or an email (lowercased)."""
    login = str(login or "").strip()

    if "@" in login:
        email = login.lower()
        if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
            raise AccountError("Enter a valid email address.")
        return email

    digits = re.sub(r"\D", "", login)

    if len(digits) == 12 and digits.startswith("91"):
        digits = digits[2:]

    if not re.fullmatch(r"[6-9]\d{9}", digits):
        raise AccountError("Enter a 10-digit mobile number or an email address.")

    return digits


def _business_gstin(gstin: str) -> str:
    gstin = clean_gstin(gstin)

    if gstin:
        valid, reason = validate_gstin(gstin)
        if not valid:
            raise AccountError(f"Business GSTIN: {reason}.")

    return gstin


# =========================================================
# USERS + SESSIONS
# =========================================================

def create_user(
    name: str,
    login: str,
    password: str,
    role: str,
    business_name: str = "",
    business_gstin: str = "",
) -> int:
    """Create an account. A shopkeeper's shop is created with it."""
    name = str(name or "").strip()[:120]
    login = normalize_login(login)
    business_name = str(business_name or "").strip()[:120]

    if not name:
        raise AccountError("Enter your name.")
    if len(password or "") < 8:
        raise AccountError("Password must be at least 8 characters.")
    if role not in ROLES:
        raise AccountError("Choose shopkeeper or CA.")
    if role == "shop" and not business_name:
        raise AccountError("Enter your shop or business name.")

    business_gstin = _business_gstin(business_gstin)

    with connect() as db:
        if db.execute("SELECT 1 FROM users WHERE login = ?", (login,)).fetchone():
            raise AccountError("An account with this mobile number or email already exists. Log in instead.")

        now = time.time()
        user_id = db.insert(
            "INSERT INTO users (name, login, password_hash, role, created) VALUES (?, ?, ?, ?, ?)",
            (name, login, hash_password(password), role, now),
        )

        if role == "shop":
            db.execute(
                "INSERT INTO businesses (user_id, name, gstin, created) VALUES (?, ?, ?, ?)",
                (user_id, business_name, business_gstin, now),
            )

        return user_id


def get_user(user_id: int) -> Optional[dict]:
    with connect() as db:
        row = db.execute(
            "SELECT id, name, login, role, notify FROM users WHERE id = ?", (user_id,)
        ).fetchone()

    if row is None:
        return None

    user = dict(row)
    user["notify"] = bool(user["notify"])
    return user


def set_notify(user_id: int, notify: bool):
    with connect() as db:
        db.execute("UPDATE users SET notify = ? WHERE id = ?", (int(bool(notify)), user_id))


# =========================================================
# PASSWORD RESET
# =========================================================

def _code_hash(user_id: int, code: str) -> str:
    return hashlib.sha256(f"{user_id}:{code}".encode()).hexdigest()


def start_password_reset(login: str):
    """
    Create a 6-digit reset code. Returns (normalized login, code), or
    None when there is no such account or a code was sent moments ago.
    """
    try:
        login = normalize_login(login)
    except AccountError:
        return None

    now = time.time()

    with connect() as db:
        user = db.execute("SELECT id FROM users WHERE login = ?", (login,)).fetchone()
        if user is None:
            return None

        previous = db.execute(
            "SELECT created FROM password_resets WHERE user_id = ?", (user["id"],)
        ).fetchone()
        if previous and now - previous["created"] < RESET_RESEND_SECONDS:
            return None

        code = f"{secrets.randbelow(10 ** 6):06d}"
        db.execute(
            "INSERT INTO password_resets (user_id, code_hash, created, expires, attempts) "
            "VALUES (?, ?, ?, ?, 0) ON CONFLICT (user_id) DO UPDATE SET code_hash = excluded.code_hash, "
            "created = excluded.created, expires = excluded.expires, attempts = 0",
            (user["id"], _code_hash(user["id"], code), now, now + RESET_CODE_MINUTES * 60),
        )

    return login, code


def finish_password_reset(login: str, code: str, password: str) -> int:
    """Set a new password with a reset code; ends all other sessions."""
    wrong = AccountError("The code is wrong or has expired. Ask for a new code.")

    if len(password or "") < 8:
        raise AccountError("Password must be at least 8 characters.")

    try:
        login = normalize_login(login)
    except AccountError:
        raise wrong

    code = str(code or "").strip()

    with connect() as db:
        row = db.execute(
            "SELECT r.user_id, r.code_hash, r.expires, r.attempts FROM password_resets r "
            "JOIN users u ON u.id = r.user_id WHERE u.login = ?",
            (login,),
        ).fetchone()

        if row is None or row["expires"] < time.time() or row["attempts"] >= RESET_MAX_ATTEMPTS:
            raise wrong

        matched = hmac.compare_digest(_code_hash(row["user_id"], code), row["code_hash"])

        if matched:
            db.execute("UPDATE users SET password_hash = ? WHERE id = ?", (hash_password(password), row["user_id"]))
            db.execute("DELETE FROM password_resets WHERE user_id = ?", (row["user_id"],))
            db.execute("DELETE FROM sessions WHERE user_id = ?", (row["user_id"],))
        else:
            # Counted outside an exception so the transaction commits it.
            db.execute(
                "UPDATE password_resets SET attempts = attempts + 1 WHERE user_id = ?", (row["user_id"],)
            )

    if not matched:
        raise wrong

    return row["user_id"]


def authenticate(login: str, password: str) -> Optional[int]:
    try:
        login = normalize_login(login)
    except AccountError:
        return None

    with connect() as db:
        row = db.execute("SELECT id, password_hash FROM users WHERE login = ?", (login,)).fetchone()

    if row is None:
        hash_password(password or "")  # same work either way, so timing doesn't reveal accounts
        return None

    return row["id"] if check_password(password or "", row["password_hash"]) else None


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create_session(user_id: int) -> str:
    token = secrets.token_urlsafe(32)
    now = time.time()

    with connect() as db:
        db.execute("DELETE FROM sessions WHERE expires < ?", (now,))
        db.execute(
            "INSERT INTO sessions (token_hash, user_id, expires) VALUES (?, ?, ?)",
            (_token_hash(token), user_id, now + SESSION_DAYS * 86400),
        )

    return token


def session_user(token: str) -> Optional[dict]:
    if not token:
        return None

    with connect() as db:
        row = db.execute(
            "SELECT users.id, users.name, users.login, users.role FROM sessions "
            "JOIN users ON users.id = sessions.user_id "
            "WHERE sessions.token_hash = ? AND sessions.expires > ?",
            (_token_hash(token), time.time()),
        ).fetchone()

    return dict(row) if row else None


def end_session(token: str):
    with connect() as db:
        db.execute("DELETE FROM sessions WHERE token_hash = ?", (_token_hash(token or ""),))


def delete_user(user_id: int):
    """Delete the account and everything saved under it."""
    with connect() as db:
        db.execute("DELETE FROM users WHERE id = ?", (user_id,))


# =========================================================
# BUSINESSES
# =========================================================

def create_business(user_id: int, name: str, gstin: str = "") -> int:
    name = str(name or "").strip()[:120]

    if not name:
        raise AccountError("Enter the business name.")

    gstin = _business_gstin(gstin)

    with connect() as db:
        count = db.execute("SELECT COUNT(*) AS n FROM businesses WHERE user_id = ?", (user_id,)).fetchone()["n"]
        if count >= MAX_BUSINESSES:
            raise AccountError(f"You can add at most {MAX_BUSINESSES} businesses.")

        if gstin and db.execute(
            "SELECT 1 FROM businesses WHERE user_id = ? AND gstin = ?", (user_id, gstin)
        ).fetchone():
            raise AccountError("You have already added a business with this GSTIN.")

        return db.insert(
            "INSERT INTO businesses (user_id, name, gstin, created) VALUES (?, ?, ?, ?)",
            (user_id, name, gstin, time.time()),
        )


def list_businesses(user_id: int) -> List[dict]:
    """Businesses with their latest check, most recently checked first."""
    with connect() as db:
        rows = db.execute(
            """
            SELECT b.id, b.name, b.gstin,
                   c.id AS last_check_id, c.created AS last_checked,
                   c.period_text, c.total_at_risk, c.recovered,
                   (SELECT COUNT(*) FROM checks WHERE business_id = b.id) AS checks
            FROM businesses b
            LEFT JOIN checks c ON c.id = (
                SELECT id FROM checks WHERE business_id = b.id ORDER BY created DESC, id DESC LIMIT 1
            )
            WHERE b.user_id = ?
            ORDER BY COALESCE(c.created, b.created) DESC
            """,
            (user_id,),
        ).fetchall()

    return [dict(row) for row in rows]


def get_business(user_id: int, business_id: int) -> Optional[dict]:
    with connect() as db:
        row = db.execute(
            "SELECT id, name, gstin FROM businesses WHERE id = ? AND user_id = ?",
            (business_id, user_id),
        ).fetchone()

    return dict(row) if row else None


def set_business_gstin(business_id: int, gstin: str):
    with connect() as db:
        db.execute("UPDATE businesses SET gstin = ? WHERE id = ?", (gstin, business_id))


def delete_business(user_id: int, business_id: int) -> bool:
    with connect() as db:
        cursor = db.execute("DELETE FROM businesses WHERE id = ? AND user_id = ?", (business_id, user_id))
        return cursor.rowcount > 0


# =========================================================
# CHECKS
# =========================================================

def _bill_key(result: dict):
    return (result["supplier_gstin"], normalize(result["bill_number"] or result["gstr2b_number"]))


def recovered_since(previous: Optional[dict], current: dict) -> dict:
    """Bills that were at risk in the previous check and are now matched."""
    if not previous:
        return {"amount": 0.0, "items": []}

    was_at_risk = {_bill_key(r): r for r in previous["results"] if r["at_risk"]}

    items = [
        {
            "supplier_name": r["supplier_name"],
            "supplier_gstin": r["supplier_gstin"],
            "bill_number": r["bill_number"],
            "amount": was_at_risk[_bill_key(r)]["at_risk"],
        }
        for r in current["results"]
        if r["status"] == "matched" and _bill_key(r) in was_at_risk
    ]

    return {"amount": round(sum(i["amount"] for i in items), 2), "items": items}


def save_check(business_id: int, data: dict, report: bytes) -> int:
    """Save a check result (adds data['recovered']) and return its id."""
    with connect() as db:
        row = db.execute(
            "SELECT result_json FROM checks WHERE business_id = ? ORDER BY created DESC, id DESC LIMIT 1",
            (business_id,),
        ).fetchone()

        data["recovered"] = recovered_since(json.loads(row["result_json"]) if row else None, data)

        return db.insert(
            "INSERT INTO checks (business_id, created, period_text, total_at_risk, claimable, "
            "recovered, result_json, report) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                business_id,
                time.time(),
                data["period_text"],
                data["total_at_risk"],
                data["claimable"],
                data["recovered"]["amount"],
                json.dumps(data),
                report,
            ),
        )


def list_checks(user_id: int, business_id: int) -> List[dict]:
    with connect() as db:
        rows = db.execute(
            "SELECT c.id, c.created, c.period_text, c.total_at_risk, c.claimable, c.recovered "
            "FROM checks c JOIN businesses b ON b.id = c.business_id "
            "WHERE c.business_id = ? AND b.user_id = ? ORDER BY c.created DESC, c.id DESC",
            (business_id, user_id),
        ).fetchall()

    return [dict(row) for row in rows]


def _owned_check(db, user_id: int, check_id: int, columns: str):
    return db.execute(
        f"SELECT {columns} FROM checks c JOIN businesses b ON b.id = c.business_id "
        "WHERE c.id = ? AND b.user_id = ?",
        (check_id, user_id),
    ).fetchone()


def get_check(user_id: int, check_id: int) -> Optional[dict]:
    with connect() as db:
        row = _owned_check(db, user_id, check_id, "c.id, c.business_id, c.created, c.result_json")

    if row is None:
        return None

    data = json.loads(row["result_json"])
    data.update(check_id=row["id"], business_id=row["business_id"], created=row["created"])
    data["reminder_status"] = reminder_statuses(row["business_id"])
    return data


def get_report(user_id: int, check_id: int) -> Optional[bytes]:
    with connect() as db:
        row = _owned_check(db, user_id, check_id, "c.report")

    return bytes(row["report"]) if row else None


def delete_check(user_id: int, check_id: int) -> bool:
    with connect() as db:
        if _owned_check(db, user_id, check_id, "c.id") is None:
            return False
        db.execute("DELETE FROM checks WHERE id = ?", (check_id,))
        return True


# =========================================================
# SUPPLIER REMINDER TRACKING
# =========================================================

def reminder_statuses(business_id: int) -> dict:
    with connect() as db:
        rows = db.execute(
            "SELECT supplier_gstin, status, updated FROM reminders WHERE business_id = ?",
            (business_id,),
        ).fetchall()

    return {row["supplier_gstin"]: {"status": row["status"], "updated": row["updated"]} for row in rows}


def set_reminder_status(business_id: int, supplier_gstin: str, status: str):
    if status not in REMINDER_STATUSES:
        raise AccountError(f"Status must be one of: {', '.join(REMINDER_STATUSES)}.")

    with connect() as db:
        db.execute(
            "INSERT INTO reminders (business_id, supplier_gstin, status, updated) VALUES (?, ?, ?, ?) "
            "ON CONFLICT (business_id, supplier_gstin) DO UPDATE SET status = excluded.status, "
            "updated = excluded.updated",
            (business_id, clean_gstin(supplier_gstin)[:15], status, time.time()),
        )


# =========================================================
# THROTTLING (failed logins, reset requests)
# =========================================================

def too_many_attempts(limits: dict, window_seconds: float) -> bool:
    """True when any key has reached its limit within the window."""
    cutoff = time.time() - window_seconds

    with connect() as db:
        for key, limit in limits.items():
            count = db.execute(
                "SELECT COUNT(*) AS n FROM attempts WHERE key = ? AND at > ?", (key, cutoff)
            ).fetchone()["n"]
            if count >= limit:
                return True

    return False


def record_attempt(*keys: str, keep_seconds: float = 86400):
    now = time.time()

    with connect() as db:
        db.execute("DELETE FROM attempts WHERE at < ?", (now - keep_seconds,))
        for key in keys:
            db.execute("INSERT INTO attempts (key, at) VALUES (?, ?)", (key, now))
