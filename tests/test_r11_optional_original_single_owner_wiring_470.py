"""#470 R11 stage6: source-only, default OFF single-owner original-frame route.

R04 optional topic failures must not GAP native LINEAR publicTrade or arm
the trading system. PostgreSQL role/schema preflight is disposable fake only.
No systemd, producer socket, database, Exchange or file writes occur in tests.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from bybit_workbench.market_data.source_required_feed_manifest import (
    SourceManifestInvalid,
)
from operations.monitoring.public_market_source_union_capture import (
    _r11_optional_frame_preflight,
    r11_optional_original_frames_enabled,
    r11_original_optional_kind,
    run_source_union,
)


class Rows:
    def __init__(self, values: object) -> None:
        self.values = values

    def fetchone(self):
        return self.values


class FakeSchemaConnection:
    def __init__(self) -> None:
        self.has_schema = True
        self.rights = (True, True, False, False)
        self.queries: list[str] = []

    def execute(self, query: str, params=()):
        self.queries.append(query)
        expected = "analytics.public_source_original_event_frames"
        if "to_regclass" in query:
            assert params == (expected,)
            return Rows((expected,) if self.has_schema else (None,))
        if "has_table_privilege" in query:
            assert params == (expected,) * 4
            return Rows(self.rights)
        raise AssertionError(f"unexpected R11 preflight query {query}")


def test_new_original_journal_is_off_by_default_and_needs_exact_flag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("CRIPTA_R11_OPTIONAL_ORIGINAL_FRAMES", raising=False)
    assert r11_optional_original_frames_enabled() is False
    monkeypatch.setenv("CRIPTA_R11_OPTIONAL_ORIGINAL_FRAMES", "0")
    assert r11_optional_original_frames_enabled() is False
    monkeypatch.setenv("CRIPTA_R11_OPTIONAL_ORIGINAL_FRAMES", "1")
    assert r11_optional_original_frames_enabled() is True
    for bad in ("yes", "true", "2", "", "OFF"):
        monkeypatch.setenv("CRIPTA_R11_OPTIONAL_ORIGINAL_FRAMES", bad)
        with pytest.raises(SourceManifestInvalid, match="FLAG_INVALID"):
            r11_optional_original_frames_enabled()


@pytest.mark.parametrize(
    "product,kind,should_persist",
    [
        ("SPOT", "TRADE", True),
        ("SPOT", "BOOK", True),
        ("SPOT", "TICKER", False),
        ("SPOT", "LIQUIDATION", False),
        ("SPOT", "FORMING_KLINE", False),
        ("LINEAR", "TRADE", False),
        ("LINEAR", "BOOK", True),
        ("LINEAR", "TICKER", True),
        ("LINEAR", "LIQUIDATION", True),
        ("LINEAR", "FORMING_KLINE", False),
        ("INVERSE", "TRADE", False),
        ("INVERSE", "BOOK", False),
    ],
)
def test_one_optional_original_event_route_never_duplicates_linear_tape(
    product: str, kind: str, should_persist: bool,
) -> None:
    assert r11_original_optional_kind(product, kind) is should_persist


def test_exact_source_schema_and_narrow_privileges_are_read_only_preflight() -> None:
    db = FakeSchemaConnection()
    _r11_optional_frame_preflight(db)
    assert len(db.queries) == 2
    assert all(
        "INSERT" not in statement and "UPDATE " not in statement
        for statement in db.queries
    )


@pytest.mark.parametrize(
    "rights",
    [
        (False, True, False, False),
        (True, False, False, False),
        (True, True, True, False),
        (True, True, False, True),
    ],
)
def test_wrong_effective_role_grants_are_not_upgraded_by_test(
    rights,
) -> None:
    db = FakeSchemaConnection()
    db.rights = rights
    with pytest.raises(SourceManifestInvalid, match="ROLE_GRANTS_INVALID"):
        _r11_optional_frame_preflight(db)
    assert len(db.queries) == 2


def test_missing_disposable_schema_fail_closed_before_source_socket() -> None:
    db = FakeSchemaConnection()
    db.has_schema = False
    with pytest.raises(SourceManifestInvalid, match="SCHEMA_NOT_INSTALLED"):
        _r11_optional_frame_preflight(db)
    assert len(db.queries) == 1


def test_closed_only_p90_source_must_never_enable_optional_journal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CRIPTA_R11_OPTIONAL_ORIGINAL_FRAMES", "1")
    with pytest.raises(
        SourceManifestInvalid, match="REQUIRES_OPTIONAL_SOURCE_UNION",
    ):
        run_source_union(
            FakeSchemaConnection(),
            p90_symbols=(
                "APTUSDT", "ARBUSDT", "DOTUSDT", "INJUSDT", "LTCUSDT",
            ),
            union=SimpleNamespace(),
            producer_release_commit="b" * 40,
            publish_seconds=False,
            closed_only=True,
            should_stop=lambda: True,
        )


def test_native_single_owner_wiring_is_default_off_and_scoped() -> None:
    code = Path(
        "operations/monitoring/public_market_source_union_capture.py"
    ).read_text()
    unit = Path(
        "operations/systemd/cripta-native-public-trade-source.service"
    ).read_text()
    assert '"CRIPTA_R11_OPTIONAL_ORIGINAL_FRAMES", "0"' in code
    assert "CRIPTA_R11_OPTIONAL_ORIGINAL_FRAMES=1" not in unit
    assert code.count("r11_optional_frame_index += 1") == 1
    assert "r11_original_optional_kind(" in code
    assert "make_r11_original_optional_frame(" in code
    assert "append_r11_original_optional_frame(" in code
    assert "transport_epoch_id=frame_epoch" in code
    assert "source_frame_number=r11_optional_frame_index" in code
    assert "reason=\"R11_ORIGINAL_FRAME_APPEND_UNVERIFIED\"" in code
    assert code.index("native._persist_frame(") < code.index(
        "append_r11_original_optional_frame("
    )
    assert "native._persist_frame(" in code
    assert "r11_original_enabled and events" in code
    assert "except (" in code and "R11OriginalFrameUnavailable" in code
    assert "product=product, topic=topic" in code
    # No new source owner, Exchange API or independent socket is created.
    assert "r11-original-source.service" not in code
    assert "source_session_id=session" in code
