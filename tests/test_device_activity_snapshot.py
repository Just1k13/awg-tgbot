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


class DeviceActivitySnapshotTests(unittest.TestCase):
    def test_render_line_recent_stale_and_no_data(self):
        from device_activity import render_device_activity_line

        now = datetime(2026, 3, 31, 12, 0, 0)
        recent = render_device_activity_line(
            device_num=1,
            has_runtime_peer=True,
            last_handshake_at=datetime(2026, 3, 31, 11, 30, 0),
            runtime_available=True,
            now=now,
        )
        stale = render_device_activity_line(
            device_num=2,
            has_runtime_peer=True,
            last_handshake_at=datetime(2026, 3, 29, 9, 0, 0),
            runtime_available=True,
            now=now,
        )
        no_data = render_device_activity_line(
            device_num=3,
            has_runtime_peer=False,
            last_handshake_at=None,
            runtime_available=True,
            now=now,
        )

        self.assertIn("активно недавно", recent)
        self.assertIn("давно не подключалось", stale)
        self.assertIn("нет данных", no_data)

    def test_render_line_runtime_unavailable(self):
        from device_activity import render_device_activity_line

        line = render_device_activity_line(
            device_num=1,
            has_runtime_peer=False,
            last_handshake_at=None,
            runtime_available=False,
            now=datetime(2026, 3, 31, 12, 0, 0),
        )
        self.assertIn("активность не определена", line)

    def test_parse_awg_show_output_reads_latest_handshake(self):
        import awg_backend

        sample = """
interface: awg0
peer: PUBKEY1
  latest handshake: 10 minutes, 5 seconds ago
  allowed ips: 10.8.1.11/32
peer: PUBKEY2
  latest handshake: (none)
  allowed ips: 10.8.1.12/32
"""
        peers = awg_backend.parse_awg_show_output(sample)
        self.assertIsNotNone(peers[0]["latest_handshake_at"])
        self.assertIsNone(peers[1]["latest_handshake_at"])


if __name__ == "__main__":
    unittest.main()
