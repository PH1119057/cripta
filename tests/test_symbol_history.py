import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from bybit_workbench.ui.symbol_history import (
    SymbolHistory,
    normalize_symbol,
    persistent_symbol_history,
)


class SymbolHistoryTests(unittest.TestCase):
    def test_remember_is_mru_deduplicated_and_persistent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "symbol_history.json"
            history = SymbolHistory(path, max_items=3)

            self.assertEqual(history.remember("uniusdt"), ("UNIUSDT",))
            self.assertEqual(history.remember("BTCUSDT"), ("BTCUSDT", "UNIUSDT"))
            self.assertEqual(
                history.remember("ETHUSDT"),
                ("ETHUSDT", "BTCUSDT", "UNIUSDT"),
            )
            self.assertEqual(
                history.remember("uniusdt"),
                ("UNIUSDT", "ETHUSDT", "BTCUSDT"),
            )
            self.assertEqual(SymbolHistory(path, max_items=3).load(), history.load())

    def test_load_tolerates_corruption_and_filters_invalid_values(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "symbol_history.json"
            path.write_text("not-json", encoding="utf-8")
            self.assertEqual(SymbolHistory(path).load(), ())

            path.write_text(
                json.dumps({"symbols": [" uniUSDT ", "bad-symbol", 123, "UNIUSDT"]}),
                encoding="utf-8",
            )
            self.assertEqual(SymbolHistory(path).load(), ("UNIUSDT",))

    def test_normalize_accepts_bybit_style_symbols(self) -> None:
        self.assertEqual(normalize_symbol("1000pepeusdt"), "1000PEPEUSDT")
        self.assertEqual(normalize_symbol("BTCUSDT"), "BTCUSDT")
        self.assertEqual(normalize_symbol("BTC/USDT"), "")

    def test_persistent_history_uses_local_appdata_and_migrates_old_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            old = root / "project" / "var" / "symbol_history.json"
            old.parent.mkdir(parents=True)
            SymbolHistory(old).remember("UNIUSDT")
            appdata = root / "LocalAppData"
            with patch.dict(os.environ, {"LOCALAPPDATA": str(appdata)}, clear=False):
                history = persistent_symbol_history(old)
            self.assertEqual(
                history.path,
                appdata / "BybitStrategyWorkbench" / "symbol_history.json",
            )
            self.assertEqual(history.load(), ("UNIUSDT",))


if __name__ == "__main__":
    unittest.main()
