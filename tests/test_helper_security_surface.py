import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bot"))


class HelperSecuritySurfaceTests(unittest.TestCase):
    def test_helper_parser_has_expected_operation_whitelist(self):
        import awg_helper

        parser = awg_helper.build_parser()
        subparsers_action = next(
            action for action in parser._actions if getattr(action, "dest", "") == "op"  # noqa: SLF001
        )
        actual = set(subparsers_action.choices.keys())  # noqa: SLF001
        expected = {
            "check-awg",
            "show",
            "genkey",
            "pubkey",
            "genpsk",
            "add-peer",
            "remove-peer",
            "qos-check",
            "qos-set",
            "qos-clear",
            "qos-sync",
            "denylist-check",
            "denylist-sync",
            "denylist-clear",
        }
        self.assertEqual(actual, expected)

    def test_load_policy_rejects_symlink(self):
        import awg_helper

        with tempfile.TemporaryDirectory() as td:
            real = Path(td) / "policy-real.json"
            real.write_text('{"container":"awg","interface":"awg0"}', encoding="utf-8")
            link = Path(td) / "policy-link.json"
            link.symlink_to(real)
            with self.assertRaises(RuntimeError):
                awg_helper._load_policy(link)  # noqa: SLF001

    def test_load_policy_rejects_group_writable_policy(self):
        import awg_helper

        with tempfile.TemporaryDirectory() as td:
            policy = Path(td) / "policy.json"
            policy.write_text('{"container":"awg","interface":"awg0"}', encoding="utf-8")
            os.chmod(policy, stat.S_IRUSR | stat.S_IWUSR | stat.S_IRGRP | stat.S_IWGRP)
            with self.assertRaises(RuntimeError):
                awg_helper._load_policy(policy)  # noqa: SLF001
