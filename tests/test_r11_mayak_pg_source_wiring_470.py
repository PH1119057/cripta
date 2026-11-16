"""R11 #470: opt-in PG-only source must bypass legacy public WS/REST."""
from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from operations.monitoring import mayak_v2
from operations.monitoring import r11_mayak_pg_observation as pg


def test_pg_mode_bypasses_legacy_schema_and_both_market_sockets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    called: list[str] = []

    def wrong(*_args, **_kwargs):
        raise AssertionError("legacy DDL/network path must not be called")

    monkeypatch.setenv("CRIPTA_MAYAK_SOURCE_MODE", "PG_PUBLICATION")
    monkeypatch.setattr(mayak_v2.Collector, "prepare", wrong)
    monkeypatch.setattr(mayak_v2.Collector, "_refresh_instrument_support", wrong)
    monkeypatch.setattr(mayak_v2.Collector, "_socket_loop", wrong)
    monkeypatch.setattr(mayak_v2.Collector, "_refresh_ratios", wrong)
    monkeypatch.setattr(
        pg, "run_r11_pg_observation", lambda _collector: called.append("pg"),
    )
    mayak_v2.Collector().run()
    assert called == ["pg"]


def test_unapproved_mode_rejected_before_network_or_schema(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CRIPTA_MAYAK_SOURCE_MODE", "PG_TRADING")
    with pytest.raises(ValueError, match="MAYAK_SOURCE_MODE_NOT_APPROVED"):
        mayak_v2.Collector().run()


def test_default_source_mode_stays_legacy_for_rollback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("CRIPTA_MAYAK_SOURCE_MODE", raising=False)
    collector = mayak_v2.Collector()
    assert os.environ.get("CRIPTA_MAYAK_SOURCE_MODE") is None
    assert collector.symbols == mayak_v2.DEFAULT_SYMBOLS


def test_pg_source_has_no_socket_rest_or_exchange_side_effects() -> None:
    path = Path(pg.__file__)
    source = path.read_text(encoding="utf-8")
    assert "websocket." not in source
    assert "urlopen(" not in source
    assert "api.bybit" not in source
    assert "run_r11_pg_observation" in source
    assert "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY" in source
    assert "require_provider_continuity=True" in source
    assert "replay.require_provider_continuity = True" in source
    assert "while not collector.stop.wait(1.0):" not in source
    assert "while not collector.stop.is_set():" in source
    assert "if snapshot is None:" in source
    assert "collector.stop.wait(1.0)" in source
    assert "source_mode == \"PG_PUBLICATION\"" in Path(
        mayak_v2.__file__
    ).read_text(encoding="utf-8")


def test_read_one_rejects_unknown_source_before_any_replay(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Fake:
        def execute(self, query: str, *_args):
            if query.startswith("SHOW transaction_"):
                return SimpleNamespace(fetchone=lambda: (
                    "repeatable read" if "isolation" in query else "on",
                ))
            return SimpleNamespace(fetchone=lambda: None)

    monkeypatch.setattr(
        pg, "collect_r11_seven_mayak_candidate",
        lambda *_args, **_kwargs: pytest.fail(
            "unknown source cannot trigger candidate replay"
        ),
    )
    with pytest.raises(pg.R11MayakPGSourceUnavailable,
                       match="ACTIVE_SOURCE_PUBLICATION_MISSING"):
        pg._read_one(
            Fake(), replay=pg.R11MayakPublicationReplay(),
            now=__import__("datetime").datetime.now(__import__("datetime").UTC),
        )


def test_pg_mode_waits_fail_closed_when_source_is_temporarily_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[object] = []

    class Stop:
        stopped = False

        def is_set(self) -> bool:
            return self.stopped

        def wait(self, delay: float) -> bool:
            calls.append(("wait", delay))
            self.stopped = True
            return True

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def execute(self, sql: str):
            assert sql == "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"
            return None

    collector = SimpleNamespace(
        stop=Stop(),
        _write_error=lambda market, exc: calls.append(
            ("error", market, str(exc))
        ),
    )
    monkeypatch.setattr(pg.psycopg, "connect", lambda *_a, **_k: Connection())
    monkeypatch.setattr(
        pg,
        "_restore_or_reset_replay",
        lambda _db: (_ for _ in ()).throw(
            pg.R11MayakPGSourceUnavailable(
                "R11_MAYAK_PG_ONE_CURRENT_NATIVE_SOURCE_REQUIRED"
            )
        ),
    )

    pg.run_r11_pg_observation(collector)

    assert calls == [
        (
            "error",
            "PG_PUBLICATION",
            "R11_MAYAK_PG_ONE_CURRENT_NATIVE_SOURCE_REQUIRED",
        ),
        ("wait", 1.0),
    ]



def test_pg_mode_retries_incomplete_cold_bootstrap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Stop:
        calls = 0
        def is_set(self) -> bool:
            return self.calls > 0
        def wait(self, seconds: float) -> None:
            assert seconds == 1.0
            self.calls += 1

    class Connection:
        def __enter__(self): return self
        def __exit__(self, *_args): return False
        def execute(self, sql: str):
            assert sql == "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"
            return None

    errors: list[str] = []
    stop = Stop()
    collector = SimpleNamespace(
        stop=stop,
        _write_error=lambda _market, exc: errors.append(str(exc)),
    )
    monkeypatch.setattr(pg.psycopg, "connect", lambda *_a, **_k: Connection())
    monkeypatch.setattr(
        pg, "_restore_or_reset_replay",
        lambda _db: (_ for _ in ()).throw(
            pg.R11MayakPGSourceUnavailable(
                "R11_MAYAK_PG_BOOTSTRAP_PUBLICATION_NOT_READY"
            )
        ),
    )
    pg.run_r11_pg_observation(collector)
    assert errors == ["R11_MAYAK_PG_BOOTSTRAP_PUBLICATION_NOT_READY"]
    assert stop.calls == 1

def test_pg_mode_keeps_nonavailability_integrity_failures_hard(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Stop:
        def is_set(self) -> bool:
            return False

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def execute(self, sql: str):
            assert sql == "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"
            return None

    errors: list[str] = []
    collector = SimpleNamespace(
        stop=Stop(),
        _write_error=lambda _market, exc: errors.append(str(exc)),
    )
    monkeypatch.setattr(pg.psycopg, "connect", lambda *_a, **_k: Connection())
    monkeypatch.setattr(
        pg,
        "_restore_or_reset_replay",
        lambda _db: (_ for _ in ()).throw(
            pg.R11MayakPGSourceUnavailable(
                "R11_MAYAK_PG_PUBLICATION_BACKLOG_EXCEEDS_BOUND"
            )
        ),
    )

    with pytest.raises(
        pg.R11MayakPGSourceUnavailable,
        match="PUBLICATION_BACKLOG_EXCEEDS_BOUND",
    ):
        pg.run_r11_pg_observation(collector)
    assert errors == ["R11_MAYAK_PG_PUBLICATION_BACKLOG_EXCEEDS_BOUND"]
