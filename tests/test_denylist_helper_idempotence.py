import io
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bot"))


class HelperDenylistIdempotenceTests(unittest.TestCase):
    def _dummy_parser(self, namespace):
        return types.SimpleNamespace(parse_args=lambda: namespace)

    def test_denylist_sync_ensures_primitives_then_flushes_and_rebuilds_rule(self):
        import awg_helper

        calls = []

        def fake_run(args, stdin_text=None):
            calls.append(("run", args, stdin_text))
            return ""

        def fake_script(script):
            calls.append(("script", script))
            return ""

        namespace = types.SimpleNamespace(op="denylist-sync", vpn_subnet="10.8.1.0/24")

        with (
            patch.object(awg_helper, "build_parser", return_value=self._dummy_parser(namespace)),
            patch.object(awg_helper, "_load_policy", return_value=("amnezia-awg", "awg0")),
            patch.object(awg_helper, "_ensure_denylist_primitives", side_effect=lambda: calls.append(("ensure",))),
            patch.object(awg_helper, "_run", side_effect=fake_run),
            patch.object(awg_helper, "_run_nft_script", side_effect=fake_script),
            patch.object(sys, "stdin", io.StringIO("203.0.113.0/24\n198.51.100.10/32\n")),
        ):
            rc = awg_helper.main()

        self.assertEqual(rc, 0)
        self.assertEqual(calls[0], ("ensure",))
        self.assertIn(("run", ["nft", "flush", "chain", "inet", "filter", "awg_forward"], None), calls)
        self.assertIn(("run", ["nft", "flush", "set", "inet", "filter", "awg_denylist"], None), calls)
        script_calls = [item[1] for item in calls if item[0] == "script"]
        self.assertTrue(any("add element inet filter awg_denylist" in script for script in script_calls))
        self.assertTrue(any('add rule inet filter awg_forward ip saddr 10.8.1.0/24 ip daddr @awg_denylist drop' in script for script in script_calls))

    def test_denylist_clear_ensures_primitives_then_flushes(self):
        import awg_helper

        calls = []

        def fake_run(args, stdin_text=None):
            calls.append(("run", args, stdin_text))
            return ""

        namespace = types.SimpleNamespace(op="denylist-clear", vpn_subnet="10.8.1.0/24")

        with (
            patch.object(awg_helper, "build_parser", return_value=self._dummy_parser(namespace)),
            patch.object(awg_helper, "_load_policy", return_value=("amnezia-awg", "awg0")),
            patch.object(awg_helper, "_ensure_denylist_primitives", side_effect=lambda: calls.append(("ensure",))),
            patch.object(awg_helper, "_run", side_effect=fake_run),
        ):
            rc = awg_helper.main()

        self.assertEqual(rc, 0)
        self.assertEqual(calls[0], ("ensure",))
        self.assertIn(("run", ["nft", "flush", "chain", "inet", "filter", "awg_forward"], None), calls)
        self.assertIn(("run", ["nft", "flush", "set", "inet", "filter", "awg_denylist"], None), calls)


if __name__ == "__main__":
    unittest.main()
