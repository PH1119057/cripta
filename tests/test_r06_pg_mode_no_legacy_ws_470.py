"""R06 guard against still-connected legacy WebSocket in the PG-only route."""

import inspect

from operations.monitoring import universal_entry_shadow


def test_r06_mode_short_circuits_all_old_entry_network_bootstrap():
    source=inspect.getsource(universal_entry_shadow.main)
    route=source.index('os.environ.get("CRIPTA_R06_PG_ONLY"')
    pg=source.index("run_pg_only(",route)
    legacy=source.index("_run_multi_strategy_observer()",pg)
    assert route < pg < legacy
    assert "CRYPTA_R06" not in source  # fail closed on misspelled operator gate


def test_r06_pg_only_status_reports_shared_market_source(monkeypatch):
    captured = {}

    def capture(_path, payload):
        captured.update(payload)

    monkeypatch.setattr(universal_entry_shadow, "_atomic_json", capture)
    universal_entry_shadow._observer_status(
        state="BLOCKED",
        epoch_id=None,
        active_bundles=(),
        symbols=(),
        fact_source_id="ms-bybit-mainnet-public-v1",
    )
    assert captured["fact_source_id"] == "ms-bybit-mainnet-public-v1"

    from operations.monitoring import r06_pg_only_entry

    pg_source = inspect.getsource(r06_pg_only_entry.run_pg_only)
    assert '"fact_source_id":_SOURCE' in pg_source
    assert pg_source.count("fact_source_id=_SOURCE") == 2
