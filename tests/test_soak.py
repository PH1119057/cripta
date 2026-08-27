import os
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

from bybit_workbench.exchange.bybit import BybitStreamProcessor, ReconnectBackoff
from bybit_workbench.persistence import EventJournal


@unittest.skipUnless(os.getenv("RUN_SOAK_TESTS") == "1", "set RUN_SOAK_TESTS=1")
class SoakTests(unittest.TestCase):
    def test_reconnect_order_deduplication_and_sqlite_contention(self) -> None:
        cycles = int(os.getenv("SOAK_CYCLES", "10000"))
        workers = 4
        now = datetime(2026, 8, 13, tzinfo=UTC)

        first = ReconnectBackoff(seed=91)
        second = ReconnectBackoff(seed=91)
        for _ in range(cycles):
            self.assertEqual(first.next_delay(), second.next_delay())
            if first.attempt % 25 == 0:
                first.reset()
                second.reset()

        processor = BybitStreamProcessor("BTCUSDT")
        order_message = {
            "id": "soak-order-message",
            "topic": "order",
            "creationTime": int(now.timestamp() * 1000),
            "data": [
                {
                    "orderId": "soak-order",
                    "orderLinkId": "soak-client",
                    "symbol": "BTCUSDT",
                    "side": "Buy",
                    "orderType": "Limit",
                    "orderStatus": "New",
                    "qty": "0.001",
                    "cumExecQty": "0",
                    "leavesQty": "0.001",
                    "price": "50000",
                    "avgPrice": "",
                    "reduceOnly": False,
                    "createdTime": str(int(now.timestamp() * 1000)),
                    "updatedTime": str(int(now.timestamp() * 1000)),
                }
            ],
        }
        self.assertEqual(len(processor.on_private(order_message, received_at=now)), 1)
        for _ in range(cycles):
            self.assertEqual(processor.on_private(order_message, received_at=now), ())
        self.assertEqual(len(processor.snapshot().orders), 1)

        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "soak.db"
            bootstrap = EventJournal(database)
            bootstrap.close()

            def write_events(worker: int) -> None:
                journal = EventJournal(database)
                try:
                    for sequence in range(cycles // workers):
                        journal.append(
                            "soak.event",
                            f"worker={worker} sequence={sequence}",
                        )
                finally:
                    journal.close()

            with ThreadPoolExecutor(max_workers=workers) as executor:
                list(executor.map(write_events, range(workers)))

            journal = EventJournal(database)
            try:
                expected = (cycles // workers) * workers
                self.assertEqual(len(journal.recent(expected + 1)), expected)
            finally:
                journal.close()


if __name__ == "__main__":
    unittest.main()
