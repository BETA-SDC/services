from contextlib import closing
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from server.backup import backup_database


class BackupTests(unittest.TestCase):
    def test_online_backup_preserves_data_permissions_and_retention(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "source.sqlite3"
            destination = Path(folder) / "backups"
            with closing(sqlite3.connect(source)) as connection, connection:
                connection.execute("CREATE TABLE registrations (username TEXT)")
                connection.execute("INSERT INTO registrations VALUES (?)", ("backup_test",))
                connection.commit()
                first = backup_database(source, destination, keep=2)
                second = backup_database(source, destination, keep=2)
                latest = backup_database(source, destination, keep=2)
            self.assertFalse(first.exists())
            self.assertTrue(second.exists())
            with closing(sqlite3.connect(latest)) as backup:
                self.assertEqual(backup.execute(
                    "SELECT username FROM registrations"
                ).fetchall(), [("backup_test",)])
            self.assertEqual(latest.stat().st_mode & 0o777, 0o600)
            self.assertEqual(destination.stat().st_mode & 0o777, 0o700)

    def test_missing_database_does_not_create_an_empty_source_or_partial_backup(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "missing.sqlite3"
            destination = Path(folder) / "backups"
            with self.assertRaises(sqlite3.OperationalError):
                backup_database(source, destination)
            self.assertFalse(source.exists())
            self.assertEqual(list(destination.iterdir()), [])
