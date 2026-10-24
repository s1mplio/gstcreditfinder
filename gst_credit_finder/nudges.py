"""Monthly "your GSTR-2B is ready" reminders.

GSTR-2B for a month is generated on the 14th of the next month. From
then on, anyone with a business not yet checked for that month gets
one message (email or SMS), at most once per month.

    python -m gst_credit_finder.nudges     # send now (also runs daily inside the website)
"""

import logging
import os
import time
from datetime import date
from typing import List, Optional, Tuple

from . import accounts, notify

log = logging.getLogger("gst_credit_finder.nudges")

GSTR2B_READY_DAY = 14

MONTHS = [
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
]


def latest_ready_period(today: date) -> Tuple[int, int]:
    """(year, month) of the newest GSTR-2B available on `today`."""
    back = 1 if today.day >= GSTR2B_READY_DAY else 2
    index = today.year * 12 + today.month - 1 - back
    return index // 12, index % 12 + 1


def due(today: Optional[date] = None) -> Tuple[str, List[dict]]:
    """
    The period ('MMYYYY') and the people to remind, each as
    {user_id, login, name, businesses: [names not checked for it]}.
    """
    year, month = latest_ready_period(today or date.today())
    period = f"{month:02d}{year}"
    shown = f"%{month:02d}-{year}%"  # as it appears in period_text

    with accounts.connect() as db:
        rows = db.execute(
            """
            SELECT u.id AS user_id, u.login, u.name, b.name AS business
            FROM users u JOIN businesses b ON b.user_id = u.id
            WHERE u.notify = 1
              AND NOT EXISTS (SELECT 1 FROM nudges n WHERE n.user_id = u.id AND n.period = ?)
              AND NOT EXISTS (
                  SELECT 1 FROM checks c WHERE c.business_id = b.id AND c.period_text LIKE ?
              )
            ORDER BY u.id, b.name
            """,
            (period, shown),
        ).fetchall()

    people = {}

    for row in rows:
        person = people.setdefault(
            row["user_id"],
            {"user_id": row["user_id"], "login": row["login"], "name": row["name"], "businesses": []},
        )
        person["businesses"].append(row["business"])

    return period, list(people.values())


def message(person: dict, period: str) -> str:
    month = f"{MONTHS[int(period[:2]) - 1]} {period[2:]}"
    names = person["businesses"]
    which = names[0] if len(names) == 1 else f"{len(names)} businesses"
    link = os.getenv("APP_URL", "").rstrip("/")

    return (
        f"Namaste {person['name'].split()[0]}, your GSTR-2B for {month} is ready on the GST portal. "
        f"Check {which} for GST credit lost to suppliers who didn't file"
        + (f": {link}" if link else ".")
    )


def send_due(today: Optional[date] = None) -> int:
    """Send this month's reminders; returns how many were sent."""
    period, people = due(today)
    sent = 0

    for person in people:
        text = message(person, period)

        try:
            notify.send(
                person["login"],
                "Your GSTR-2B is ready: check your GST credit",
                text,
                sms_template_env="MSG91_NUDGE_TEMPLATE",
                sms_vars={
                    "month": f"{MONTHS[int(period[:2]) - 1]} {period[2:]}",
                    "count": str(len(person["businesses"])),
                    "link": os.getenv("APP_URL", ""),
                },
            )
        except notify.NotifyError:
            continue  # try again tomorrow

        with accounts.connect() as db:
            db.execute(
                "INSERT INTO nudges (user_id, period, sent) VALUES (?, ?, ?) ON CONFLICT DO NOTHING",
                (person["user_id"], period, time.time()),
            )
        sent += 1

    return sent


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print(f"Sent {send_due()} reminder(s).")
