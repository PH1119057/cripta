"""Published confirmed boundaries are independent of R03 preparation grace."""
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from bybit_workbench.market_data.entry_publication_reader import EntryPublicationPin
from bybit_workbench.market_identity import BYBIT_PUBLIC_MARKET_SOURCE_ID, bybit_instrument_id
from operations.monitoring import r06_pinned_seed as mod

BOUNDARY = datetime(2026, 11, 12, 16, tzinfo=UTC)


def pin(second=1, lag_tf=None):
    close = {tf: (BOUNDARY-timedelta(minutes=int(tf)) if tf == lag_tf else BOUNDARY
                  ).isoformat() for tf in ('5', '15')}
    return EntryPublicationPin(30, 's', 'p', BOUNDARY+timedelta(seconds=second),
                               {'LTCUSDT': {'event_at': BOUNDARY.isoformat()}},
                               {'LTCUSDT': close})


@pytest.mark.parametrize('second', [0, 1, 5, 14, 15, 16, 59])
def test_exact_published_close_never_subtracts_grace_or_reads_newer(monkeypatch, second):
    calls = []
    class DB:
        def execute(self, sql, params):
            assert sql.startswith('SELECT')
            assert params == ('LTCUSDT',)
            return SimpleNamespace(fetchall=lambda: [(
                BYBIT_PUBLIC_MARKET_SOURCE_ID, 'venue-bybit',
                bybit_instrument_id('linear', 'LTCUSDT'), 'LTCUSDT', 'LINEAR',
            )])
    class Store:
        def __init__(self, _db, key):
            self.tf = key.timeframe
        def history(self, *, through, required):
            calls.append((self.tf, through, required))
            return ('closed',)*required
    monkeypatch.setattr(mod, 'SharedClosedCandleStore', Store)
    result = mod.read_pinned_closed_seed(DB(), pin=pin(second), symbol='LTCUSDT',
                                        required_counts={'5': 2600, '15': 1000})
    assert calls == [('5', BOUNDARY, 2600), ('15', BOUNDARY, 1000)]
    assert len(result['5']) == 2600
    assert mod.scope_geometry_ready(pin(second), ('LTCUSDT',))


@pytest.mark.parametrize('lag_tf', ['5', '15'])
def test_native_prefix_waits_for_its_own_confirmed_close(lag_tf):
    current = pin(lag_tf=lag_tf)
    assert not mod.scope_geometry_ready(current, ('LTCUSDT',))
    current.native_watermarks['INJUSDT'] = {'event_at': BOUNDARY.isoformat()}
    current.closed_refs['INJUSDT'] = dict(pin().closed_refs['LTCUSDT'])
    # A different decision scope remains independently usable.
    assert mod.scope_geometry_ready(current, ('INJUSDT',))


def test_future_native_time_is_not_a_scoped_wait():
    current = pin()
    current.native_watermarks['LTCUSDT']['event_at'] = (
        BOUNDARY+timedelta(seconds=2)).isoformat()
    with pytest.raises(mod.R06PinnedSeedBlocked, match='NATIVE_TIME_INVALID'):
        mod.scope_geometry_ready(current, ('LTCUSDT',))


def test_within_publication_second_is_not_a_future_event():
    current = pin(second=0)
    current.native_watermarks['LTCUSDT']['event_at'] = (
        BOUNDARY+timedelta(milliseconds=500)).isoformat()
    assert mod.scope_geometry_ready(current, ('LTCUSDT',))
