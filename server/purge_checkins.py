"""Legacy entry point: expire transient grants only, never attendance records."""
import os
from pathlib import Path
import sqlite3
from contextlib import closing

from server.registration.checkin import purge_expired


def main():
    path = Path(os.environ.get("REGISTRATION_DATABASE", "/var/lib/betasdc/registrations.sqlite3"))
    with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=rw", uri=True)) as connection, connection:
        purge_expired(connection)


if __name__ == "__main__":
    main()
