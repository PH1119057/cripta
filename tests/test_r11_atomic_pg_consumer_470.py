"""#470 R11: one transaction for MAYAK+Dispatcher full seven panel and cursor."""
from __future__ import annotations

from contextlib import nullcontext
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from bybit_workbench.mayak.native_trade_replay_parity import R11ReplayCursor
from bybit_workbench.mayak.optional_original_event_parity import (
    R11OptionalMayakCursor,
)
from bybit_workbench.mayak.seven_publication_atomic_cycle import (
    R11MayakDurableCommitUnverified,
    persist_r11_mayak_original_cycle,
)
from bybit_workbench.mayak.seven_publication_engine_checkpoint import (
    encode_r11_mayak_state,
)
from bybit_workbench.mayak.seven_publication_observation_candidate import (
    R11SevenMayakReplayCheckpoint,
)
from bybit_workbench.mayak.seven_publication_replay_runtime import (
    R11MayakPublicationReplay,
)

AT = datetime(2026, 11, 14, 18, 30, tzinfo=UTC)
SESSION = "a" * 32
RELEASE = "b" * 40
SEVEN = ("APTUSDT", "ARBUSDT", "DOTUSDT", "INJUSDT",
         "LTCUSDT", "BTCUSDT", "ETHUSDT")


def pair() -> tuple[dict[str, Any], bytes, R11SevenMayakReplayCheckpoint]:
    cursor = R11SevenMayakReplayCheckpoint(
        publication_id=42, source_session_id=SESSION,
        release_commit=RELEASE, as_of_second=AT,
        optional_cursors=tuple(
            R11OptionalMayakCursor(
                source_session_id=SESSION,
                transport_epoch_id=("c" if product == "SPOT" else "d") * 32,
                product_category=product,
                producer_release_commit=RELEASE,
                next_cursor=1, next_digest="e"*64, books=(),
            ) for product in ("SPOT", "LINEAR")
        ),
        native_cursors=tuple(
            R11ReplayCursor(
                symbol=symbol, source_session_id=SESSION,
                producer_release_commit=RELEASE,
                publication_id=42, publication_as_of=AT,
                publication_watermark=(1, 0), cursor=(1, 0),
                last_seq=100, last_event_at=AT-timedelta(seconds=1),
                recent_exec_ids=(),
            ) for symbol in SEVEN
        ),
    )
    replay = R11MayakPublicationReplay(
        checkpoint=cursor, first_publication_at=AT,
        observations=1,
    )
    snapshot = replay.engine.snapshot(AT+timedelta(seconds=3))
    snapshot["r11_market_publication"] = {
        "publication_id": 42, "source_session_id": SESSION,
        "producer_release_commit": RELEASE,
        "consumer_ready": False,
        "mayak_ws_retirement_allowed": False,
    }
    snapshot["dispatcher_handoff"]["provenance"][
        "r11_market_publication_id"
    ] = 42
    snapshot["dispatcher_handoff"]["provenance"][
        "r11_source_session_id"
    ] = SESSION
    return snapshot, encode_r11_mayak_state(replay), cursor


class Fake:
    def __init__(self, *, fail: str = "") -> None:
        self.commands: list[str] = []
        self.commit = False
        self.fail = fail
        self.next_coin = 0

    def transaction(self):
        return nullcontext()

    def execute(self, query: str, _params: Any = None):
        self.commands.append(query.strip())
        if "FROM analytics.native_public_trade_source_sessions" in query:
            return Result(
                rows=([(SESSION, RELEASE)] if self.fail != "gap" else [])
            )
        if "FROM analytics.market_publications" in query:
            return Result(one=(SESSION, RELEASE, AT))
        if "FROM mayak_v2.r11_market_publication_checkpoints" in query:
            return Result(one=(
                (42, SESSION, RELEASE, AT)
                if self.fail == "previous"
                else None
            ))
        if "INSERT INTO mayak_v2.snapshots" in query:
            return Result(one=(551,))
        if "INSERT INTO mayak_v2.coin_market_contexts" in query:
            self.next_coin += 1
            return Result(one=("coin-context",))
        if "INSERT INTO mayak_v2.shared_market_contexts" in query:
            return Result(one=("market-context",))
        if "INSERT INTO mayak_v2.r11_market_publication_checkpoints" in query:
            self.commit = True
        return Result()

    def cursor(self):
        return self

    def executemany(self, query: str, data: Any):
        self.commands.append("BATCH:" + query[:60])
        assert len(data) == 14


class Result:
    def __init__(self, *, one: Any = None, rows: Any = None):
        self.one = one
        self.rows = rows or []

    def fetchone(self):
        return self.one

    def fetchall(self):
        return self.rows


def test_one_tx_contains_seven_coin_contexts_and_dispatcher_and_source_cursor():
    snapshot, binary, cursor = pair()
    db = Fake()
    sid = persist_r11_mayak_original_cycle(
        db, snapshot=snapshot, engine_state=binary,
        previous=None, current=cursor,
    )
    assert sid == 551
    assert db.next_coin == 7
    assert db.commit
    sql = " ".join(db.commands)
    assert "pg_advisory_xact_lock" in sql
    assert "FOR SHARE" in sql
    assert "mayak_v2.snapshots" in sql
    assert "mayak_v2.coin_market_contexts" in sql
    assert "mayak_v2.shared_market_contexts" in sql
    assert "mayak_v2.observation_journal" in sql
    assert "market_minutes" in sql
    assert "r11_market_publication_checkpoints" in sql


@pytest.mark.parametrize("failure", [
    "gap", "previous", "source_scope", "false_ready", "wrong_binary",
])
def test_unproven_source_or_resume_cannot_commit_derived_contexts(
    failure: str,
) -> None:
    snapshot, binary, cursor = pair()
    db = Fake(fail=failure)
    if failure == "source_scope":
        snapshot["r11_market_publication"]["source_session_id"] = "f"*32
    elif failure == "false_ready":
        snapshot["r11_market_publication"]["consumer_ready"] = True
    elif failure == "wrong_binary":
        binary = binary[:-10]
    with pytest.raises(
        (R11MayakDurableCommitUnverified, Exception)
    ):
        persist_r11_mayak_original_cycle(
            db, snapshot=snapshot, engine_state=binary,
            previous=None, current=cursor,
        )
    assert not db.commit
