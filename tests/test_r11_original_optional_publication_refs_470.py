"""#470 R11: source-only original-frame refs with strict temporal boundaries."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from bybit_workbench.market_identity import BYBIT_PUBLIC_MARKET_SOURCE_ID
from bybit_workbench.market_data.r11_original_publication_refs import (
    R11PublicationOriginalRefsUnverified,
    collect_r11_original_optional_publication_refs,
)

S = "a" * 32
E1 = "b" * 32
E2 = "c" * 32
R = "d" * 40
SHA = "e" * 64
AT = datetime(2026, 11, 14, 15, 0, tzinfo=UTC)


class Result:
    def __init__(self, value: tuple[object, ...] | None) -> None:
        self.value = value

    def fetchone(self) -> tuple[object, ...] | None:
        return self.value


class DB:
    def __init__(self) -> None:
        self.rows: dict[str, tuple[object, ...] | None] = {
            "SPOT": (
                BYBIT_PUBLIC_MARKET_SOURCE_ID, R, 13, SHA,
                AT - timedelta(milliseconds=20),
                AT - timedelta(milliseconds=30),
            ),
            "LINEAR": (
                BYBIT_PUBLIC_MARKET_SOURCE_ID, R, 14, SHA,
                AT - timedelta(milliseconds=30),
                AT - timedelta(milliseconds=40),
            ),
        }
        self.queries: list[tuple[str, object]] = []

    def execute(self, sql: str, params: object = None) -> Result:
        self.queries.append((sql, params))
        assert "analytics.public_source_original_event_frames" in sql
        assert isinstance(params, tuple) and len(params) == 4
        assert params[0] == S and params[3] == AT
        assert params[1] == {"SPOT": E1, "LINEAR": E2}[params[2]]
        return Result(self.rows[params[2]])


def test_source_identity_frame_hash_and_asof_pinned_per_product() -> None:
    db = DB()
    refs, quality = collect_r11_original_optional_publication_refs(
        db, source_session_id=S, producer_release_commit=R,
        transport_epochs={"SPOT": E1, "LINEAR": E2},
        as_of_second=AT,
    )
    assert set(refs) == {"SPOT", "LINEAR"}
    assert refs["SPOT"]["source_frame_number"] == 13
    assert refs["LINEAR"]["source_frame_number"] == 14
    assert refs["SPOT"]["frame_content_sha256"] == SHA
    assert refs["SPOT"]["transport_epoch_id"] == E1
    assert refs["LINEAR"]["transport_epoch_id"] == E2
    assert quality == {"SPOT": "PHYSICAL_FRAME_REF_ONLY",
                       "LINEAR": "PHYSICAL_FRAME_REF_ONLY"}
    assert len(db.queries) == 2
    assert all(sql.lstrip().upper().startswith("SELECT") for sql, _ in db.queries)


def test_missing_product_is_explicit_unknown_not_continuity() -> None:
    db = DB()
    db.rows["SPOT"] = None
    refs, quality = collect_r11_original_optional_publication_refs(
        db, source_session_id=S, producer_release_commit=R,
        transport_epochs={"SPOT": E1, "LINEAR": E2},
        as_of_second=AT,
    )
    assert "SPOT" not in refs
    assert quality["SPOT"] == "NO_ORIGINAL_FRAME_IN_CURRENT_EPOCH"
    assert quality["LINEAR"] == "PHYSICAL_FRAME_REF_ONLY"


@pytest.mark.parametrize("bad", [
    "wrong_release", "wrong_source", "bad_hash", "bad_frame",
    "future_received", "future_provider", "naive_received",
    "provider_after_receipt",
])
def test_invalid_physical_frame_never_publishes_verified_ref(
    bad: str,
) -> None:
    db = DB()
    v = list(db.rows["SPOT"] or ())
    if bad == "wrong_release":
        v[1] = "f" * 40
    elif bad == "wrong_source":
        v[0] = "unexpected-market"
    elif bad == "bad_hash":
        v[3] = "g" * 64
    elif bad == "bad_frame":
        v[2] = 0
    elif bad == "future_received":
        v[4] = AT + timedelta(seconds=1)
    elif bad == "future_provider":
        v[5] = AT + timedelta(seconds=1)
    elif bad == "naive_received":
        v[4] = AT.replace(tzinfo=None)
    elif bad == "provider_after_receipt":
        v[5] = AT + timedelta(seconds=6)
    db.rows["SPOT"] = tuple(v)
    with pytest.raises(R11PublicationOriginalRefsUnverified):
        collect_r11_original_optional_publication_refs(
            db, source_session_id=S, producer_release_commit=R,
            transport_epochs={"SPOT": E1, "LINEAR": E2},
            as_of_second=AT,
        )


@pytest.mark.parametrize("invalid", [
    {"SPOT": E1},
    {"SPOT": E1, "LINEAR": "not-epoch"},
    {"SPOT": E1, "LINEAR": E2, "INVERSE": E1},
])
def test_incomplete_or_extra_transport_epoch_not_accepted(
    invalid: dict[str, str],
) -> None:
    with pytest.raises(R11PublicationOriginalRefsUnverified):
        collect_r11_original_optional_publication_refs(
            DB(), source_session_id=S, producer_release_commit=R,
            transport_epochs=invalid, as_of_second=AT,
        )


def test_future_boundary_not_rounded_or_invented() -> None:
    with pytest.raises(R11PublicationOriginalRefsUnverified):
        collect_r11_original_optional_publication_refs(
            DB(), source_session_id=S, producer_release_commit=R,
            transport_epochs={"SPOT": E1, "LINEAR": E2},
            as_of_second=AT + timedelta(microseconds=1),
        )
