"""Send one-time codes and reminders by email or SMS.

A login that contains '@' gets email; a mobile number gets SMS.

    Email (SMTP):  SMTP_HOST, SMTP_PORT (587), SMTP_USER, SMTP_PASSWORD, MAIL_FROM
    SMS (MSG91):   MSG91_AUTH_KEY, MSG91_OTP_TEMPLATE, MSG91_NUDGE_TEMPLATE

Indian SMS must use DLT-registered templates, so SMS text comes from
the MSG91 template and only the variables are sent from here.

Without a provider configured, messages are written to the server log
(development mode), so the whole app works before any account is set up.
"""

import json
import logging
import os
import smtplib
import urllib.request
from collections import deque
from email.message import EmailMessage

log = logging.getLogger("gst_credit_finder.notify")

MSG91_FLOW_URL = "https://control.msg91.com/api/v5/flow"

# Last messages written in development mode (read by the tests).
outbox = deque(maxlen=100)


class NotifyError(RuntimeError):
    pass


def _email_configured() -> bool:
    return bool(os.getenv("SMTP_HOST") and os.getenv("MAIL_FROM"))


def _sms_configured() -> bool:
    return bool(os.getenv("MSG91_AUTH_KEY"))


def send(login: str, subject: str, text: str, sms_template_env: str = "", sms_vars: dict = None):
    """Send `text` to a login (email or 10-digit mobile number)."""
    if "@" in login:
        if _email_configured():
            return _send_email(login, subject, text)
    elif _sms_configured() and os.getenv(sms_template_env):
        return _send_sms(login, os.getenv(sms_template_env), sms_vars or {})

    outbox.append({"to": login, "subject": subject, "text": text, "vars": sms_vars or {}})
    log.warning("No email/SMS provider set up. Message for %s: %s", login, text)


def _send_email(to: str, subject: str, text: str):
    message = EmailMessage()
    message["From"] = os.getenv("MAIL_FROM")
    message["To"] = to
    message["Subject"] = subject
    message.set_content(text)

    try:
        with smtplib.SMTP(os.getenv("SMTP_HOST"), int(os.getenv("SMTP_PORT", "587")), timeout=20) as smtp:
            smtp.starttls()
            if os.getenv("SMTP_USER"):
                smtp.login(os.getenv("SMTP_USER"), os.getenv("SMTP_PASSWORD", ""))
            smtp.send_message(message)
    except (OSError, smtplib.SMTPException) as error:
        log.error("Email to %s failed: %s", to, error)
        raise NotifyError("Couldn't send the email. Please try again in a few minutes.")


def _send_sms(mobile: str, template_id: str, variables: dict):
    body = json.dumps({
        "template_id": template_id,
        "short_url": "0",
        "recipients": [{"mobiles": f"91{mobile}", **variables}],
    }).encode()

    request = urllib.request.Request(
        MSG91_FLOW_URL,
        data=body,
        headers={"authkey": os.getenv("MSG91_AUTH_KEY"), "Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            reply = json.loads(response.read() or b"{}")
    except (OSError, ValueError) as error:
        log.error("SMS to %s failed: %s", mobile, error)
        raise NotifyError("Couldn't send the SMS. Please try again in a few minutes.")

    if reply.get("type") == "error":
        log.error("SMS to %s rejected: %s", mobile, reply)
        raise NotifyError("Couldn't send the SMS. Please try again in a few minutes.")
