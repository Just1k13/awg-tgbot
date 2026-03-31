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

    def test_recommended_actions_do_not_reference_removed_safe_update_menu_item(self):
        result = run_bash(
            f'''
            export AWG_TGBOT_SOURCE_ONLY=1
            source "{SCRIPT}"
            STARTUP_STATE_CODE="awg_yes_bot_yes"
            print_recommended_actions
            '''
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Переустановить", result.stdout)
        self.assertNotIn("Безопасно обновить", result.stdout)

    def test_update_status_line_points_to_reinstall_menu_item(self):
        result = run_bash(
            f'''
            export AWG_TGBOT_SOURCE_ONLY=1
            source "{SCRIPT}"
            STATE_BOT_INSTALLED=1
            UPDATE_STATUS="available"
            UPDATE_LOCAL_SHA="1111111111111111111111111111111111111111"
            UPDATE_REMOTE_SHA="2222222222222222222222222222222222222222"
            print_update_status_line
            '''
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Открой пункт меню: 3) Переустановить", result.stdout)
        self.assertNotIn("Безопасно обновить", result.stdout)


if __name__ == "__main__":
    unittest.main()
