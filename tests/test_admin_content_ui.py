import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bot"))

os.environ.setdefault("ENCRYPTION_SECRET", "test-secret")
os.environ.setdefault("API_TOKEN", "123:test")
os.environ.setdefault("ADMIN_ID", "1")
os.environ.setdefault("SERVER_PUBLIC_KEY", "A" * 44)
os.environ.setdefault("SERVER_IP", "1.1.1.1:51820")


class AdminMenuMvpTests(unittest.TestCase):
    def test_admin_keyboard_keeps_only_mvp_sections(self):
        from keyboards import get_admin_inline_kb

        kb = get_admin_inline_kb()
        labels = [button.text for row in kb.inline_keyboard for button in row]

        self.assertIn("👥 Пользователи", labels)
        self.assertIn("📊 Статистика", labels)
        self.assertIn("🎁 Рефералы", labels)
        self.assertIn("🔄 Синхронизация", labels)
        self.assertIn("💸 Цены", labels)
        self.assertIn("💾 Бэкап", labels)
        self.assertIn("📢 Рассылка", labels)

        self.assertNotIn("📝 Тексты", labels)
        self.assertNotIn("⚙️ Настройки", labels)
        self.assertNotIn("🧹 Очистить потерянные peer", labels)
        self.assertNotIn("❤️ Health", labels)


if __name__ == "__main__":
    unittest.main()
