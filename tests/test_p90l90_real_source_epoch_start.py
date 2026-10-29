"""No-order integration proof: real WS, signed flat accounts and OHLC gates.

The independent sources are explicit test doubles, never production claims.
None of these tests obtains a LIVE arm or an Exchange mutation.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from bybit_workbench.universal_entry import p90l90_real_source_epoch_start as module
ALL = frozenset(("LTCUSDT", "INJUSDT", "DOTUSDT", "ARBUSDT", "APTUSDT"))
MAIN = "ta-2122c81ecff14f3b85b102db605212f7"
SUB = "ta-853ac5fcd58185697396d33cd57459fa"
SHA = "a" * 40


class _Result:
    def __init__(self, rows=(), row=None, rowcount=1):
        self.rows = rows
        self.row = row
        self.rowcount = rowcount

    def fetchall(self):
        return self.rows

    def fetchone(self):
        return self.row


class _DB:
    def __init__(self, *, exposure=False, existing=()):
        self.info = SimpleNamespace(
            transaction_status=SimpleNamespace(name="INTRANS"),
        )
        self.exposure = exposure
        self.existing = existing
        self.sql = []

    def execute(self, sql, args=()):
        self.sql.append((sql, args))
        if sql == "SHOW transaction_isolation":
            return _Result(row=("serializable",))
        if sql == "SHOW transaction_read_only":
            return _Result(row=("off",))
        if "SELECT count(*)" in sql:
            return _Result(row=(1 if self.exposure else 0,))
        if "WHERE status='ACTIVE' FOR UPDATE" in sql:
            return _Result(rows=[(x,) for x in self.existing])
        if "FROM identity.market_sources" in sql:
            return _Result(rows=[
                ("ms-bybit-mainnet-public-v1", "venue-bybit",
                 "instrument-" + symbol, symbol, "LINEAR")
                for symbol in sorted(ALL)
            ])
        if "INSERT INTO strategy_entry.p90l90_real_source_epochs" in sql:
            return _Result()
        if "UPDATE strategy_entry.p90l90_real_source_epochs" in sql:
            return _Result(rowcount=1)
        raise AssertionError("unexpected SQL " + sql)


def _ready():
    now = datetime.now(UTC).replace(second=2, microsecond=0)
    started = now - timedelta(seconds=3)
    mirror = now - timedelta(seconds=2)
    subscribed = now - timedelta(seconds=1)
    proof = module.P90RealNewWsSourceProof(
        source_commit=SHA,
        subscribed_symbols=ALL, subscribed_intervals=frozenset(("5", "15")),
        mirror_active=True, subscribed_local_at=subscribed,
        subscribed_server_at=subscribed, epoch_started_at=started,
    )
    main = module.P90RealSignedFlatAccount(
        MAIN, "111111111", "main-generation", "bybit:signed-uid:main",
        now, 0, 0, 0,
    )
    sub = module.P90RealSignedFlatAccount(
        SUB, "222222222", "sub-generation", "bybit:signed-uid:sub",
        now, 0, 0, 0,
    )
    return proof, main, sub, mirror, now


def _stub(monkeypatch):
    seeded = []
    def geometry(_fives, _fifteens, *, effective_at):
        return SimpleNamespace(effective_at=effective_at)
    class Shared:
        def __init__(self, db, key):
            self.key = key
        def history(self, *, through, required):
            return [SimpleNamespace(closed_at=through)] * required
    class Atomic:
        def __init__(self, db, *, source_commit, writer_release_commit, epoch_id):
            assert source_commit == writer_release_commit == SHA
        def seed(self, *, symbol, last_closed_5m, last_closed_15m,
                 entry, proven_geometry):
            assert entry.state is proven_geometry
            seeded.append(symbol)
    monkeypatch.setattr(module, "SharedClosedCandleStore", Shared)
    monkeypatch.setattr(module, "P90L90AtomicRealEpochCheckpointStore", Atomic)
    monkeypatch.setattr(module, "causal_entry_state", geometry)
    return seeded


def test_fresh_five_symbol_epoch_uses_only_shared_candle_source_and_new_rows(
    monkeypatch,
) -> None:
    seeded = _stub(monkeypatch)
    source, main, sub, mirror, now = _ready()
    db = _DB()
    epoch_id = module.start_p90_real_source_epoch(
        db, source_commit=SHA, source=source,
        signed_accounts=(main, sub), mirror_ready_at=mirror,
        new_epoch_requested_at=now,
    )
    assert epoch_id.startswith("p90-real-epoch-")
    assert len(epoch_id) == len("p90-real-epoch-") + 40
    assert seeded == sorted(ALL)
    statements = [statement for statement, _ in db.sql]
    assert any("INSERT INTO strategy_entry.p90l90_real_source_epochs" in s
               for s in statements)
    assert all("p90l90_causal_transport_checkpoints" not in s for s in statements)
    assert all("p90l90_causal_closed_bars" not in s for s in statements)
    assert all("UPDATE control.execution_gates" not in s for s in statements)


@pytest.mark.parametrize("failure", (
    "wrong-uid", "single-account", "not-flat", "stale-private",
    "no-mirror", "local-exposure", "old-epoch",
))
def test_source_epoch_denies_incomplete_market_or_account_evidence(
    monkeypatch, failure: str,
) -> None:
    seeded = _stub(monkeypatch)
    source, main, sub, mirror, now = _ready()
    db = _DB(exposure=failure == "local-exposure",
             existing=("old-real-epoch",) if failure == "old-epoch" else ())
    accounts = (main, sub)
    if failure == "wrong-uid":
        sub = module.P90RealSignedFlatAccount(
            SUB, main.signed_uid, sub.private_generation_id, sub.source_ref,
            sub.observed_at, 0, 0, 0,
        )
        accounts = (main, sub)
    elif failure == "single-account":
        accounts = (main, main)
    elif failure == "not-flat":
        sub = module.P90RealSignedFlatAccount(
            SUB, sub.signed_uid, sub.private_generation_id, sub.source_ref,
            sub.observed_at, 1, 0, 0,
        )
        accounts = (main, sub)
    elif failure == "stale-private":
        sub = module.P90RealSignedFlatAccount(
            SUB, sub.signed_uid, sub.private_generation_id, sub.source_ref,
            now - timedelta(minutes=4), 0, 0, 0,
        )
        accounts = (main, sub)
    elif failure == "no-mirror":
        source = module.P90RealNewWsSourceProof(
            source_commit=source.source_commit,
            subscribed_symbols=source.subscribed_symbols,
            subscribed_intervals=source.subscribed_intervals,
            mirror_active=False,
            subscribed_local_at=source.subscribed_local_at,
            subscribed_server_at=source.subscribed_server_at,
            epoch_started_at=source.epoch_started_at,
        )
    with pytest.raises(module.P90RealEpochStartUnverified):
        module.start_p90_real_source_epoch(
            db, source_commit=SHA, source=source,
            signed_accounts=accounts, mirror_ready_at=mirror,
            new_epoch_requested_at=now,
        )
    assert seeded == []
    assert all("INSERT INTO" not in sql for sql, _ in db.sql)
