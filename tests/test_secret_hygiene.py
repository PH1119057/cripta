import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class SecretHygieneTests(unittest.TestCase):
    def test_source_contains_no_embedded_key_or_secret_values(self) -> None:
        assignments = re.compile(
            r"(?i)(api[_-]?(?:key|secret)|authorization)\s*=\s*['\"][^'\"]{8,}['\"]"
        )
        offenders = []
        for path in (ROOT / "src").rglob("*.py"):
            if assignments.search(path.read_text(encoding="utf-8")):
                offenders.append(str(path.relative_to(ROOT)))
        self.assertEqual(offenders, [])

    def test_no_withdrawal_endpoint_exists_in_exchange_adapter(self) -> None:
        exchange_source = "\n".join(
            path.read_text(encoding="utf-8").lower()
            for path in (ROOT / "src" / "bybit_workbench" / "exchange").rglob("*.py")
        )
        self.assertNotIn("/v5/asset/withdraw", exchange_source)


if __name__ == "__main__":
    unittest.main()
