import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bot"))

os.environ.setdefault("ENCRYPTION_SECRET", "test-secret")
os.environ.setdefault("API_TOKEN", "123:test")
os.environ.setdefault("ADMIN_ID", "1")
os.environ.setdefault("SERVER_PUBLIC_KEY", "A" * 44)
os.environ.setdefault("SERVER_IP", "1.1.1.1:51820")
os.environ.setdefault("SUPPORT_USERNAME", "@support_test")
os.environ.setdefault("AWG_HELPER_POLICY_PATH", str(ROOT / "tests" / "helper-policy.json"))


class RuntimeSmokecheckTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        import database

        self.tmp = tempfile.TemporaryDirectory()
        database.DB_PATH = str(Path(self.tmp.name) / "test.db")
        await database.close_shared_db()
        await database.init_db()

    async def asyncTearDown(self):
        import database

        await database.close_shared_db()
        self.tmp.cleanup()

    async def test_runtime_smokecheck_success_path(self):
        import handlers_admin

        async def fake_db_health_info():
            return {"is_healthy": True}

        async def fake_check_awg_container():
            return None

        with (
            patch.object(handlers_admin, "DOCKER_CONTAINER", "amnezia-awg2"),
            patch.object(handlers_admin, "WG_INTERFACE", "awg0"),
            patch.object(handlers_admin, "AWG_HELPER_POLICY_PATH", "/etc/awg-bot-helper.json"),
            patch.object(handlers_admin, "db_health_info", fake_db_health_info),
            patch.object(handlers_admin, "check_awg_container", fake_check_awg_container),
            patch.object(handlers_admin, "read_helper_policy", return_value=("amnezia-awg2", "awg0", "")),
        ):
            report = await handlers_admin.run_runtime_smokecheck()

        self.assertEqual(report["overall"], "ok")
        states = {item["name"]: item["state"] for item in report["checks"]}
        self.assertEqual(states["DB"], "ok")
        self.assertEqual(states["AWG target"], "ok")
        self.assertEqual(states["Helper policy"], "ok")

    async def test_runtime_smokecheck_helper_policy_mismatch_is_degraded(self):
        import handlers_admin

        async def fake_db_health_info():
            return {"is_healthy": True}

        async def fake_check_awg_container():
            return None

        with (
            patch.object(handlers_admin, "DOCKER_CONTAINER", "amnezia-awg2"),
            patch.object(handlers_admin, "WG_INTERFACE", "awg0"),
            patch.object(handlers_admin, "AWG_HELPER_POLICY_PATH", "/etc/awg-bot-helper.json"),
            patch.object(handlers_admin, "db_health_info", fake_db_health_info),
            patch.object(handlers_admin, "check_awg_container", fake_check_awg_container),
            patch.object(handlers_admin, "read_helper_policy", return_value=("other-container", "awg0", "")),
        ):
            report = await handlers_admin.run_runtime_smokecheck()

        self.assertEqual(report["overall"], "warning")
        helper = next(item for item in report["checks"] if item["name"] == "Helper policy")
        self.assertEqual(helper["state"], "warning")
        self.assertIn("mismatch", helper["detail"])

    async def test_health_command_returns_smokecheck_text(self):
        import handlers_admin

        class DummyMessage:
            def __init__(self):
                self.answers = []

            async def answer(self, text, **kwargs):
                self.answers.append((text, kwargs))

        async def fake_build_runtime_smokecheck_text():
            return "smoke output"

        msg = DummyMessage()
        with patch.object(handlers_admin, "build_runtime_smokecheck_text", fake_build_runtime_smokecheck_text):
            await handlers_admin.health_cmd(msg)  # type: ignore[arg-type]

        self.assertEqual(msg.answers[-1][0], "smoke output")
        self.assertEqual(msg.answers[-1][1].get("parse_mode"), "HTML")


if __name__ == "__main__":
    unittest.main()
