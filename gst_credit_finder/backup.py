"""Copy the database to a dated backup file, keeping the newest few.

    python -m gst_credit_finder.backup      # back up now (also runs daily inside the website)

BACKUP_DIR (default data/backups) and BACKUP_KEEP (default 14) control
where and how many. A backup on the same disk doesn't survive the disk
failing: copy BACKUP_DIR somewhere else too (see README).
"""

import os
import sqlite3
import time
from pathlib import Path

from . import accounts

PREFIX = "gst_credit_finder-"


def backup_dir() -> Path:
    return Path(os.getenv("BACKUP_DIR", Path(accounts.DB_PATH).parent / "backups"))


def run_backup() -> Path:
    """Write a consistent copy of the live database and prune old copies."""
    folder = backup_dir()
    folder.mkdir(parents=True, exist_ok=True)

    target = folder / f"{PREFIX}{time.strftime('%Y%m%d-%H%M%S')}.db"

    with accounts.connect() as live:
        copy = sqlite3.connect(target)
        try:
            live.backup(copy)  # safe while the website is running
        finally:
            copy.close()

    keep = max(1, int(os.getenv("BACKUP_KEEP", "14")))
    for old in sorted(folder.glob(f"{PREFIX}*.db"))[:-keep]:
        old.unlink()

    return target


if __name__ == "__main__":
    print(f"Backed up to {run_backup()}")
