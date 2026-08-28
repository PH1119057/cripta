import threading
from datetime import UTC, datetime

from bybit_workbench.mayak.core.live import LiveMayakEngine


def test_snapshot_is_safe_while_trades_arrive() -> None:
    engine = LiveMayakEngine(("BTCUSDT",))
    now = datetime.now(UTC).timestamp()
    errors: list[Exception] = []

    def writer() -> None:
        try:
            for index in range(5000):
                engine.on_trade("linear", "BTCUSDT", now, "Buy", 100, index + 1)
        except Exception as exc:  # pragma: no cover - diagnostic capture
            errors.append(exc)

    thread = threading.Thread(target=writer)
    thread.start()
    for _ in range(200):
        engine.snapshot(datetime.now(UTC))
    thread.join()
    assert not errors
