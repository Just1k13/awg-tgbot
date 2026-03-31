import os
import sys
import unittest
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bot"))

os.environ.setdefault("ENCRYPTION_SECRET", "test-secret")
os.environ.setdefault("API_TOKEN", "123:test")
os.environ.setdefault("ADMIN_ID", "1")
os.environ.setdefault("SERVER_PUBLIC_KEY", "A" * 44)
os.environ.setdefault("SERVER_IP", "1.1.1.1:51820")


class AdminBackupFlowTests(unittest.TestCase):
    def test_build_backup_file_path_insecure(self):
        import handlers_admin

        path = handlers_admin._build_backup_file_path(
            "/tmp/awg-tgbot/db.sqlite",
            secure_mode=False,
            now=datetime(2026, 3, 31, 10, 20, 30),
        )
        self.assertEqual(path.name, "redacted_vpn_bot_20260331_102030.sqlite")
        self.assertEqual(path.parent.name, "backups")

    def test_build_backup_file_path_secure_suffix(self):
        import handlers_admin

        path = handlers_admin._build_backup_file_path(
            "/tmp/awg-tgbot/db.sqlite",
            secure_mode=True,
            now=datetime(2026, 3, 31, 10, 20, 30),
        )
        self.assertEqual(path.name, "redacted_vpn_bot_20260331_102030.sqlite.enc")

    def test_build_backup_result_message_renders_path_and_mode(self):
        import handlers_admin

        msg = handlers_admin._build_backup_result_message(
            Path("/srv/awg-tgbot/data/backups/redacted_vpn_bot_20260331_102030.sqlite"),
            secure_mode=False,
        )
        self.assertIn("Backup created", msg)
        self.assertIn("/srv/awg-tgbot/data/backups/redacted_vpn_bot_20260331_102030.sqlite", msg)
        self.assertIn("Mode: <b>insecure</b>", msg)


if __name__ == "__main__":
    unittest.main()
