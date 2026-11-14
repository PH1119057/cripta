"""#470 R09: two-port PG/native parity can never substitute signed OPEN Exit."""
from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

from bybit_workbench.market_data.p90l90_real_publication_parity import (
    R09PublicationParityUnverified,
    require_r09_broker_publication_parity,
)
from bybit_workbench.market_data.p90l90_real_publication_postfill import (
    P90PGPostfillUnverified,
    R09NativePublicationCandidate,
)
from bybit_workbench.universal_exit.p90l90_real_observation import (
    P90RealPostfillSourceWindow,
)
from operations.connectivity import p90_real_independent_exit as real_exit

AT = datetime(2026, 11, 14, 11, 10, tzinfo=UTC)
SOURCE_REFS = (
    "bybit:publicTrade:ARBUSDT:one",
    "bybit:publicTrade:ARBUSDT:two",
)


def source_candidate() -> R09NativePublicationCandidate:
    return R09NativePublicationCandidate(
        position_id="POS-r09", symbol="ARBUSDT",
        source_session_id="a" * 32, publication_id=19,
        fill_at=AT - timedelta(seconds=5), covered_through=AT,
        source_commit="c" * 40, producer_release_commit="b" * 40,
        last_native_price=Decimal("0.1720"),
        observed_favorable_price=Decimal("0.1720"),
        native_event_count=2, source_refs=SOURCE_REFS,
    )


def source_broker() -> P90RealPostfillSourceWindow:
    return P90RealPostfillSourceWindow(
        position_id="POS-r09", symbol="ARBUSDT",
        fill_at=AT - timedelta(seconds=5),
        covered_through=AT - timedelta(seconds=2),
        source_commit="c" * 40,
        last_native_price=Decimal("0.1720"),
        best_favorable_price=Decimal("0.1720"),
        source_refs=SOURCE_REFS,
        complete_native_window=True,
        no_new_native_trade_rest_verified=False,
    )


def test_two_port_parity_returns_evidence_but_never_exit_ready() -> None:
    candidate, broker = source_candidate(), source_broker()
    checked = require_r09_broker_publication_parity(
        candidate=candidate, broker_window=broker,
    )
    assert checked.position_id == candidate.position_id
    assert checked.source_session_id == candidate.source_session_id
    assert checked.publication_id == 19
    assert checked.source_refs == SOURCE_REFS
    assert checked.financial_exit_ready is False


@pytest.mark.parametrize(
    "candidate_change,broker_change",
    [
        ({"exit_ready": True}, {}),
        ({"native_continuity_verified": True}, {}),
        ({}, {"complete_native_window": False}),
        ({}, {"no_new_native_trade_rest_verified": True}),
        ({"position_id": "POS-other"}, {}),
        ({}, {"position_id": "POS-other"}),
        ({"symbol": "BTCUSDT"}, {}),
        ({}, {"symbol": "BTCUSDT"}),
        ({"source_commit": "d" * 40}, {}),
        ({}, {"source_commit": "d" * 40}),
        ({"native_event_count": 1}, {}),
        ({"source_refs": SOURCE_REFS[:1]}, {}),
        ({}, {"source_refs": SOURCE_REFS[:1]}),
        ({}, {"last_native_price": Decimal("0.1715")}),
        ({"observed_favorable_price": Decimal("0.1715")}, {}),
        ({}, {"best_favorable_price": Decimal("0.1715")}),
        ({}, {"covered_through": AT + timedelta(seconds=2)}),
        ({}, {"covered_through": AT - timedelta(seconds=8)}),
        ({"source_refs": SOURCE_REFS[:1] * 2}, {}),
    ],
)
def test_original_source_divergence_never_promotes_financial_exit(
    candidate_change: dict[str, object],
    broker_change: dict[str, object],
) -> None:
    with pytest.raises(
        R09PublicationParityUnverified,
        match="R09_PUBLICATION_BROKER_ORIGINAL_NATIVE_PARITY_UNVERIFIED",
    ):
        require_r09_broker_publication_parity(
            candidate=replace(source_candidate(), **candidate_change),
            broker_window=replace(source_broker(), **broker_change),
        )


def test_strict_r09_source_broker_outage_never_falls_back_to_public_rest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(real_exit, "R09_PARITY_MODE", "REQUIRE")

    def unavailable(**_kwargs):
        raise real_exit.P90RealPostfillSourceClientUnverified(
            "P90_REAL_POSTFILL_SOURCE_TRANSPORT_UNAVAILABLE"
        )

    monkeypatch.setattr(
        real_exit, "read_real_postfill_source_window", unavailable,
    )
    from bybit_workbench.market_data import p90l90_real_durable_postfill_source

    def forbidden(*_args, **_kwargs):
        raise AssertionError("R09 strict never launches a second Bybit REST")

    monkeypatch.setattr(
        p90l90_real_durable_postfill_source,
        "read_independent_durable_postfill_window", forbidden,
    )
    with pytest.raises(
        real_exit.P90IndependentExitBlocked,
        match="R09_ORIGINAL_NATIVE_BROKER_UNAVAILABLE",
    ):
        real_exit.read_firstfill_source_with_independent_market_fallback(
            object(), position=SimpleNamespace(), source_epoch_id="epoch",
            source_commit="c" * 40,
            accepted_producer_commits=frozenset(("b" * 40,)),
        )


def test_r09_reader_uses_separate_repeatable_read_read_only_pg_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Conn:
        def __init__(self) -> None:
            self.commands: list[str] = []
            self.closed = False

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            self.closed = True

        def transaction(self):
            return self

        def execute(self, sql: str):
            self.commands.append(sql)

    source_db = Conn()
    seen: list[str] = []
    monkeypatch.setattr(
        real_exit.psycopg, "connect",
        lambda *args, **kwargs: (
            seen.append(f"autocommit={kwargs.get('autocommit')}") or source_db
        ),
    )

    def reader(db, **kwargs):
        assert db is source_db
        assert kwargs["source_epoch_id"] == "epoch"
        assert kwargs["source_commit"] == "c" * 40
        assert kwargs["accepted_producer_commits"] == frozenset(("b" * 40,))
        assert kwargs["observed_at"] == AT
        seen.append("read")
        return source_candidate()

    monkeypatch.setattr(real_exit, "read_r09_native_publication_postfill", reader)
    real_exit.require_r09_original_postfill_source_parity(
        position=SimpleNamespace(),
        source_epoch_id="epoch",
        source_commit="c" * 40,
        accepted_producer_commits=frozenset(("b" * 40,)),
        broker_window=source_broker(),
        observed_at=AT,
    )
    assert source_db.closed is True
    assert source_db.commands == [
        "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"
    ]
    assert seen == ["autocommit=True", "read"]


def test_failed_source_read_prevents_new_exit_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Conn:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def transaction(self):
            return self

        def execute(self, _sql):
            return None

    monkeypatch.setattr(real_exit.psycopg, "connect", lambda *_a, **_kw: Conn())

    def failed(*_args, **_kwargs):
        raise P90PGPostfillUnverified("R09_PG_POSTFILL_MISSING")

    monkeypatch.setattr(real_exit, "read_r09_native_publication_postfill", failed)
    with pytest.raises(
        real_exit.P90IndependentExitBlocked,
        match="R09_ORIGINAL_PUBLICATION_BROKER_PARITY_UNVERIFIED",
    ):
        real_exit.require_r09_original_postfill_source_parity(
            position=SimpleNamespace(), source_epoch_id="epoch",
            source_commit="c" * 40,
            accepted_producer_commits=frozenset(("b" * 40,)),
            broker_window=source_broker(), observed_at=AT,
        )


def test_r09_enforce_requires_source_fence_before_signed_private_exit() -> None:
    runtime = Path(
        "operations/connectivity/p90_real_independent_exit.py"
    ).read_text()
    main = runtime.split("def run_p90_exit_once(", 1)[1]
    assert (
        main.index("require_r07_dispatch_epoch_guard(")
        < main.index("read_firstfill_source_with_independent_market_fallback(")
        < main.index("require_r09_original_postfill_source_parity(")
        < main.index("private_client = P90PrivateProofClient(")
    )
    assert 'R09_PARITY_MODE == "REQUIRE"' in main
    assert 'R09_PARITY_MODE = os.environ.get(' in runtime
    assert '"DISABLED"' in runtime
    assert "ORDER" not in Path(
        "src/bybit_workbench/market_data/p90l90_real_publication_parity.py"
    ).read_text()
