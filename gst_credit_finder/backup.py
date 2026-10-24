"""Copy the SQLite database to a dated backup file, keeping the newest few.

With Postgres (DATABASE_URL), the database provider keeps backups
(Neon: point-in-time restore), so this does nothing.

    python -m gst_credit_finder.backup      # back up now (also runs daily inside the website)

BACKUP_DIR (default data/backups) and BACKUP_KEEP (default 14) control
where and how many. A backup on the same disk doesn't survive the disk
failing: copy BACKUP_DIR somewhere else too (see README).
"""

import os
import sqlite3
import time
from pathlib import Path

from . import db

PREFIX = "gst_credit_finder-"


def backup_dir() -> Path:
    return Path(os.getenv("BACKUP_DIR", Path(db.DB_PATH).parent / "backups"))


def run_backup():
    """Write a consistent copy of the live database and prune old copies."""
    if db.is_postgres():
        return None

    folder = backup_dir()
    folder.mkdir(parents=True, exist_ok=True)

    target = folder / f"{PREFIX}{time.strftime('%Y%m%d-%H%M%S')}.db"

    with db.connect() as live:
        copy = sqlite3.connect(target)
        try:
            live.raw.backup(copy)  # safe while the website is running
        finally:
            copy.close()

    keep = max(1, int(os.getenv("BACKUP_KEEP", "14")))
    for old in sorted(folder.glob(f"{PREFIX}*.db"))[:-keep]:
        old.unlink()

    return target


if __name__ == "__main__":
    target = run_backup()
    print(f"Backed up to {target}" if target else "Postgres: backups are kept by the database provider.")
