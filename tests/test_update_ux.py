import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "awg-tgbot.sh"


def run_bash(snippet: str) -> subprocess.CompletedProcess[str]:
    cmd = f"set -euo pipefail\n{snippet}"
    return subprocess.run(["bash", "-lc", cmd], cwd=ROOT, text=True, capture_output=True)


class InstallerMvpUxTests(unittest.TestCase):
    def test_update_action_is_disabled_in_mvp(self):
        result = run_bash(
            f'''
            export AWG_TGBOT_SOURCE_ONLY=1
            source "{SCRIPT}"
            run_action update
            '''
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("отключена в personal MVP", result.stderr)

    def test_check_updates_action_is_disabled_in_mvp(self):
        result = run_bash(
            f'''
            export AWG_TGBOT_SOURCE_ONLY=1
            source "{SCRIPT}"
            run_action check-updates
            '''
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("отключена в personal MVP", result.stderr)


if __name__ == "__main__":
    unittest.main()
