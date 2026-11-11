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
