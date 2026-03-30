import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "awg-tgbot.sh"


def run_bash(snippet: str) -> subprocess.CompletedProcess[str]:
    cmd = f"set -euo pipefail\n{snippet}"
    return subprocess.run(["bash", "-lc", cmd], cwd=ROOT, text=True, capture_output=True)


class UpdateUxTests(unittest.TestCase):
    def test_menu_uses_detected_pinned_sha(self):
        result = run_bash(
            f'''
            export AWG_TGBOT_SOURCE_ONLY=1
            source "{SCRIPT}"
            detect_install_state() {{ STATE_BOT_INSTALLED=1; return 0; }}
            refresh_update_status_quiet() {{
              UPDATE_STATUS="available"
              UPDATE_LOCAL_SHA="1111111111111111111111111111111111111111"
              UPDATE_REMOTE_SHA="2222222222222222222222222222222222222222"
              UPDATE_REMOTE_TITLE="Update title"
              UPDATE_SAFE_READY=1
              return 0
            }}
            prompt_menu_key() {{ printf -v "$2" '%s' "1"; }}
            if menu_choose_update_ref; then
              echo "OK:$REPO_UPDATE_REF"
            else
              echo "NO"
            fi
            '''
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("OK:2222222222222222222222222222222222222222", result.stdout)

    def test_menu_show_command_option(self):
        result = run_bash(
            f'''
            export AWG_TGBOT_SOURCE_ONLY=1
            source "{SCRIPT}"
            detect_install_state() {{ STATE_BOT_INSTALLED=1; return 0; }}
            refresh_update_status_quiet() {{
              UPDATE_STATUS="available"
              UPDATE_LOCAL_SHA="1111111111111111111111111111111111111111"
              UPDATE_REMOTE_SHA="3333333333333333333333333333333333333333"
              UPDATE_SAFE_READY=1
              return 0
            }}
            prompt_menu_key() {{ printf -v "$2" '%s' "2"; }}
            if menu_choose_update_ref; then
              echo "UNEXPECTED_SUCCESS"
            else
              echo "MENU_EXIT"
            fi
            '''
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("MENU_EXIT", result.stdout)
        self.assertIn("sudo REPO_UPDATE_REF=3333333333333333333333333333333333333333 awg-tgbot update", result.stdout)

    def test_menu_explains_when_pinned_unavailable(self):
        result = run_bash(
            f'''
            export AWG_TGBOT_SOURCE_ONLY=1
            source "{SCRIPT}"
            detect_install_state() {{ STATE_BOT_INSTALLED=1; return 0; }}
            refresh_update_status_quiet() {{
              UPDATE_STATUS="unknown"
              UPDATE_LOCAL_SHA="1111111111111111111111111111111111111111"
              UPDATE_REMOTE_SHA=""
              UPDATE_SAFE_READY=0
              return 0
            }}
            if menu_choose_update_ref; then
              echo "UNEXPECTED_SUCCESS"
            else
              echo "MENU_EXIT"
            fi
            '''
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("MENU_EXIT", result.stdout)
        self.assertIn("Авто-подготовка pinned update сейчас недоступна", result.stdout)
        self.assertIn("REPO_UPDATE_REF=<40-hex-sha>", result.stdout)

    def test_update_bot_menu_runs_with_detected_sha(self):
        result = run_bash(
            f'''
            export AWG_TGBOT_SOURCE_ONLY=1
            source "{SCRIPT}"
            detect_install_state() {{ STATE_BOT_INSTALLED=1; return 0; }}
            prompt_menu_key() {{ printf -v "$2" '%s' "1"; }}
            refresh_update_status_quiet() {{
              UPDATE_STATUS="available"
              UPDATE_LOCAL_SHA="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
              UPDATE_REMOTE_SHA="bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
              UPDATE_REMOTE_TITLE="Update title"
              UPDATE_SAFE_READY=1
              return 0
            }}
            ensure_packages() {{ return 0; }}
            ensure_docker_ready() {{ return 0; }}
            create_update_backup() {{ echo "/tmp/update-backup"; }}
            download_repo() {{ echo "/tmp/repo:$1"; }}
            stop_service_if_exists() {{ return 0; }}
            deploy_repo() {{ return 0; }}
            ensure_env_file() {{ return 0; }}
            get_env_value() {{ return 0; }}
            ensure_secret() {{ echo "secret"; }}
            prompt_api_token() {{ :; }}
            prompt_admin_id() {{ :; }}
            write_common_env() {{ return 0; }}
            detect_awg_environment() {{ return 0; }}
            write_detected_awg_env() {{ return 0; }}
            ensure_venv_and_requirements() {{ return 0; }}
            ensure_bot_user() {{ return 0; }}
            install_awg_helper() {{ return 0; }}
            write_service() {{ return 0; }}
            persist_repo_branch() {{ return 0; }}
            mkdir() {{ command mkdir "$@"; }}
            start_service() {{ return 0; }}
            systemctl() {{ echo "active"; return 0; }}
            show_status() {{ echo "SHOW_STATUS"; }}
            update_bot menu
            '''
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Pinned ref для обновления: bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb", result.stderr)
        self.assertIn("SHOW_STATUS", result.stdout)


if __name__ == "__main__":
    unittest.main()
