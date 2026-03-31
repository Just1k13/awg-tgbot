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
os.environ.setdefault("SUPPORT_USERNAME", "@support_test")


class ProfileLastPaymentSummaryTests(unittest.TestCase):
    def test_profile_includes_last_payment_summary_when_payment_exists(self):
        import handlers_user

        fields = handlers_user._build_last_payment_fields(
            {
                "payload": "sub_30",
                "created_at": "2026-03-31T10:15:00",
                "amount": 990,
                "currency": "XTR",
                "status": "applied",
            }
        )

        self.assertEqual(fields["payment_tariff"], "30 дней")
        self.assertEqual(fields["payment_date"], "31.03.2026 10:15")
        self.assertEqual(fields["payment_amount"], "990 XTR")
        self.assertEqual(fields["payment_status"], "успешно")

    def test_profile_behaves_normally_when_no_payment_exists(self):
        import handlers_user

        fields = handlers_user._build_last_payment_fields(None)

        self.assertEqual(fields["payment_tariff"], "нет данных")
        self.assertEqual(fields["payment_date"], "—")
        self.assertEqual(fields["payment_amount"], "—")
        self.assertEqual(fields["payment_status"], "—")


if __name__ == "__main__":
    unittest.main()
