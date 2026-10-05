from pathlib import Path


SOURCE = Path("operations/connectivity/private_runtime.py").read_text(encoding="utf-8")


def test_live_signed_reads_have_separate_window_from_mutations() -> None:
    assert 'SIGNED_RECV_WINDOW = "5000"' in SOURCE
    assert 'SIGNED_READ_RECV_WINDOW = "10000"' in SOURCE
    assert 'SIGNED_READ_TIMEOUT_SECONDS = 5.0' in SOURCE
    assert 'f"{timestamp}{key}{SIGNED_READ_RECV_WINDOW}{query}"' in SOURCE
    assert '"X-BAPI-RECV-WINDOW": SIGNED_READ_RECV_WINDOW' in SOURCE
    assert 'f"{timestamp}{key}{SIGNED_RECV_WINDOW}{body}"' in SOURCE


def test_periodic_position_mode_refresh_is_staggered() -> None:
    assert (
        "symbols = [symbol for symbol in symbols if symbol not in recent][:1]"
        in SOURCE
    )
    assert "CRIPTA_POSITION_MODE_FRESHNESS_SECONDS" in SOURCE


def test_periodic_read_failure_keeps_private_ws_connected() -> None:
    start = SOURCE.index("def private_loop(")
    body = SOURCE[start:]
    periodic = body.index('reconcile(\n                            connection, key, secret, "periodic"')
    degraded = body.index("except ExchangeReadUnavailable as exc:", periodic)
    outer = body.index("except Exception as exc:", degraded)
    assert periodic < degraded < outer
    segment = body[periodic:outer]
    assert '"reconciliation_degraded"' in segment
    assert "disarm_new_entries(" not in segment
    assert '"state": "connected"' in segment
    assert "next_reconcile = time.monotonic() + 1.0" in segment


def test_periodic_reconcile_uses_start_to_start_cadence() -> None:
    assert "reconcile_started_monotonic = time.monotonic()" in SOURCE
    assert "reconcile_started_monotonic + cadence" in SOURCE
    assert "time.monotonic() + 0.25" in SOURCE


def test_read_rejections_preserve_exact_bybit_reason() -> None:
    assert "reconciliation read rejected:" in SOURCE
    assert "retCode={payload.get('retCode')}" in SOURCE
    assert "retMsg={payload.get('retMsg')}" in SOURCE
    assert "position-mode probe rejected for {symbol}:" in SOURCE
