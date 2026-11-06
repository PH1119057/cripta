"""Actual operator registration and source generation on disposable PG only."""
from __future__ import annotations

import os
import shutil
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from test_p90_real_source_release_compatibility import (
    AT,
    EPOCH,
    LOADED,
    ORIGIN,
    SYMBOLS,
    bundles,
    checkpoints,
)

from bybit_workbench.domain.models import Candle
from bybit_workbench.market_data.shared_closed_candles import (
    MarketCandleKey,
    SharedClosedCandleStore,
)
from bybit_workbench.universal_entry import p90l90_real_source_compatibility as compat
from bybit_workbench.universal_entry.contracts import FrozenPolicy, MarketFactEnvelope
from bybit_workbench.universal_entry.fingerprint import canonical_json
from bybit_workbench.universal_entry.p90l90_atomic_real_epoch_checkpoint import (
    P90L90AtomicRealEpochCheckpointStore,
)
from bybit_workbench.universal_entry.p90l90_causal_fact_codec import encode_p90l90_fact
from bybit_workbench.universal_entry.p90l90_durable_paper_processor import (
    P90L90DurablePaperProcessor,
)
from bybit_workbench.universal_entry.p90l90_legacy_strategy import MAIN_ID
from bybit_workbench.universal_entry.p90l90_real_contract_v3 import VERSION
from bybit_workbench.universal_entry.p90l90_real_open_restart import (
    inspect_p90_real_open_restart_epoch,
)
from bybit_workbench.universal_entry.p90l90_real_shared_candle_ledger import (
    P90RealSharedCandleLedger,
)
from bybit_workbench.universal_entry.transport_continuity import TradeCursor

DSN = os.environ.get("CRIPTA_TRADE_LIFECYCLE_TEST_DSN")
pytestmark = pytest.mark.skipif(not DSN, reason="explicit disposable PostgreSQL only")
TABLE = "strategy_entry.p90l90_real_source_release_compatibility"


def roots(tmp_path):
    package = Path("src/bybit_workbench")
    for sha in (ORIGIN, LOADED):
        root = tmp_path / sha
        for folder in ("universal_entry", "universal_exit"):
            shutil.copytree(package / folder, root / package / folder,
                            ignore=shutil.ignore_patterns("__pycache__"))
        for name in ("strategy_position.py", "exchange_position_identity.py",
                     "domain/models.py", "market_data/shared_closed_candles.py"):
            target = root / package / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(package / name, target)
        (root / "INSTALLED_COMMIT").write_text(sha)
    return tmp_path / LOADED, tmp_path / ORIGIN


@pytest.fixture
def pg():
    psycopg = pytest.importorskip("psycopg")
    assert DSN is not None
    c = psycopg.connect(DSN)
    assert c.info.dbname != "cripta", "production forbidden"
    try:
        c.execute(
            """INSERT INTO strategy_entry.p90l90_real_source_epochs(
                   epoch_id,source_commit,market_source_id,opened_at,
                   source_ws_ready_at,native_mirror_ready_at)
               VALUES(%s,%s,'ms-bybit-mainnet-public-v1',%s,%s,%s)""",
            (EPOCH, ORIGIN, AT, AT, AT),
        )
        for symbol, snapshot, entry, origin, generation, writer in checkpoints():
            c.execute(
                """INSERT INTO strategy_entry.p90l90_real_causal_checkpoints(
                      epoch_id,symbol,checkpoint_json,entry_checkpoint_json,
                      source_commit,generation,writer_release_commit)
                   VALUES(%s,%s,%s::jsonb,%s::jsonb,%s,%s,%s)""",
                (EPOCH, symbol, canonical_json(snapshot), canonical_json(entry),
                 origin, generation, writer),
            )
        # This temp projection supplies only the startup selector's position
        # inventory. All source/checkpoint/compatibility SQL uses real tables.
        c.execute("CREATE TEMP TABLE compat_positions(position_id text,state text,"
                  "strategy_id text,symbol text,trading_account_id text,strategy_version text)")
        c.execute("GRANT SELECT,INSERT ON compat_positions TO cripta")
        c.execute("SET SESSION AUTHORIZATION cripta")
        assert c.execute("SELECT session_user,current_user").fetchone() == ("cripta", "cripta")
        for symbol in SYMBOLS:
            for tf in ("5", "15"):
                key = MarketCandleKey("ms-bybit-mainnet-public-v1", "venue-bybit",
                                      "inst-bybit-linear-" + symbol.lower(), symbol, "LINEAR", tf)
                candle = Candle(symbol, tf, AT - timedelta(minutes=int(tf)), AT,
                                Decimal("100"), Decimal("101"), Decimal("99"),
                                Decimal("100"), Decimal("1"))
                SharedClosedCandleStore(c, key).ingest(
                    (candle,), producer_release_commit=ORIGIN,
                    transport="BYBIT_REST_KLINE", observed_at=AT,
                )
        yield c
    finally:
        c.rollback()
        c.close()


def register(c, pair):
    compat.register_real_source_compatibility(
        c, epoch_id=EPOCH, loaded_commit=LOADED, bundles=bundles(),
        current_root=pair[0], origin_root=pair[1],
    )


def test_actual_pg_registration_preserves_origin_and_records_new_writer(pg, tmp_path):
    pair = roots(tmp_path)
    before = pg.execute(
        "SELECT source_commit,generation,checkpoint_json FROM "
        "strategy_entry.p90l90_real_causal_checkpoints ORDER BY symbol",
    ).fetchall()
    register(pg, pair)
    identity = compat.inspect_real_source_identity(
        pg, epoch_id=EPOCH, loaded_commit=LOADED, bundles=bundles(),
    )
    assert identity.origin_commit == ORIGIN and identity.loaded_commit == LOADED
    assert pg.execute("SELECT source_commit,generation,checkpoint_json FROM "
                      "strategy_entry.p90l90_real_causal_checkpoints ORDER BY symbol"
                      ).fetchall() == before
    assert pg.execute(f"SELECT count(*) FROM {TABLE} WHERE epoch_id=%s", (EPOCH,)
                      ).fetchone() == (1,)
    normal_actions = []
    observations = []
    processor = P90L90DurablePaperProcessor(
        pg, source_commit=ORIGIN,
        atomic_checkpoint_store=P90L90AtomicRealEpochCheckpointStore(
            pg, source_commit=ORIGIN, epoch_id=EPOCH, writer_release_commit=LOADED,
        ),
        bar_ledger=P90RealSharedCandleLedger(pg, epoch_id=EPOCH, source_commit=ORIGIN),
        on_native_exit=lambda *_: normal_actions.append("EXIT"),
        on_closed_candle=lambda *_: normal_actions.append("CANDLE"),
    )
    processor.recover(symbol="LTCUSDT")
    at = AT + timedelta(seconds=1)
    fact = MarketFactEnvelope(
        fact_id="trade-new-release-101", event_kind="PUBLIC_TRADE", symbol="LTCUSDT",
        event_at=at, observed_at=at, received_at=at,
        source_refs=("bybit:publicTrade:LTCUSDT:new-release-101",),
        attributes=FrozenPolicy.from_mapping({"price": "100.1", "size": "0.1",
                                             "taker_side": "Buy"}),
    )
    processor.accept_recovery(
        encode_p90l90_fact(fact, cursor=TradeCursor("LTCUSDT", "new-release-101", 101, at)),
        on_recovered_native=lambda fact, cursor: observations.append((fact.fact_id, cursor.seq)),
    )
    assert normal_actions == [] and observations == [(fact.fact_id, 101)]
    assert pg.execute(
        "SELECT source_commit,writer_release_commit,generation FROM "
        "strategy_entry.p90l90_real_causal_checkpoints WHERE epoch_id=%s AND symbol=%s",
        (EPOCH, "LTCUSDT"),
    ).fetchone() == (ORIGIN, LOADED, 4)
    # Registration is idempotent AFTER generation advance; original proof is
    # retained rather than overwritten with a later current-state snapshot.
    checksum = pg.execute(f"SELECT checkpoint_digest FROM {TABLE} WHERE epoch_id=%s", (EPOCH,)
                          ).fetchone()
    register(pg, pair)
    assert pg.execute(f"SELECT checkpoint_digest FROM {TABLE} WHERE epoch_id=%s", (EPOCH,)
                      ).fetchone() == checksum


@pytest.mark.parametrize("count", [1, 60])
def test_actual_pg_open_inventory_selector_uses_registered_release(pg, tmp_path, count):
    register(pg, roots(tmp_path))
    for i in range(count):
        pg.execute("INSERT INTO compat_positions VALUES(%s,%s,%s,%s,%s,%s)",
                   ("pos-" + str(i), "OPEN", MAIN_ID, "LTCUSDT", "account-" + str(i), VERSION))

    class ScopedInventory:
        def execute(self, sql, params=()):
            return pg.execute(sql.replace("runtime.position_ownership", "compat_positions"), params)

    assert inspect_p90_real_open_restart_epoch(
        ScopedInventory(), source_commit=LOADED, bundles=bundles(),
    ) == EPOCH


def test_actual_pg_append_only_acl_and_changed_consumer_block(pg, tmp_path):
    psycopg = pytest.importorskip("psycopg")
    pair = roots(tmp_path)
    changed = pair[0] / "src/bybit_workbench/universal_exit/p90l90_real_mfe.py"
    changed.write_bytes(changed.read_bytes() + b"\n# incompatible consumer bytes\n")
    with pytest.raises(compat.P90RealSourceCompatibilityUnverified,
                       match="ORIGIN_CONSUMER_CONTRACT_CHANGED"):
        register(pg, pair)
    assert pg.execute(f"SELECT count(*) FROM {TABLE} WHERE epoch_id=%s", (EPOCH,)
                      ).fetchone() == (0,)
    assert pg.execute("SELECT has_table_privilege(current_user,%s,%s),"
                      "has_table_privilege(current_user,%s,%s),"
                      "has_table_privilege(current_user,%s,%s)",
                      (TABLE, "SELECT,INSERT", TABLE, "UPDATE", TABLE, "DELETE")
                      ).fetchone() == (True, False, False)
    with pytest.raises(psycopg.errors.InsufficientPrivilege), pg.transaction():
        pg.execute(f"DELETE FROM {TABLE}")
