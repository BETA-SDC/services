#!/usr/bin/python3
"""Create an online SQLite backup and retain a bounded set of private files."""
from contextlib import closing
from datetime import datetime, timezone
import os
from pathlib import Path
import sqlite3
import tempfile


def backup_database(source, destination, keep=14):
    if keep < 1:
        raise ValueError("Backup retention must be positive")
    source, destination = Path(source), Path(destination)
    destination.mkdir(parents=True, exist_ok=True, mode=0o700)
    destination.chmod(0o700)
    name = "registrations-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + ".sqlite3"
    descriptor, temporary_name = tempfile.mkstemp(prefix=".backup-", dir=destination)
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        with closing(sqlite3.connect(source.resolve().as_uri() + "?mode=ro", uri=True)) as original:
            with closing(sqlite3.connect(temporary)) as copy:
                original.backup(copy)
                if copy.execute("PRAGMA quick_check").fetchone() != ("ok",):
                    raise RuntimeError("SQLite backup integrity check failed")
        temporary.chmod(0o600)
        result = destination / name
        os.replace(temporary, result)
        backups = sorted(destination.glob("registrations-*.sqlite3"), reverse=True)
        for expired in backups[keep:]:
            if expired.is_file() and not expired.is_symlink():
                expired.unlink()
        return result
    finally:
        temporary.unlink(missing_ok=True)


if __name__ == "__main__":
    backup_database(
        "/var/lib/betasdc/registrations.sqlite3",
        "/var/backups/betasdc/database",
    )
    print("Registration database backup verified")
