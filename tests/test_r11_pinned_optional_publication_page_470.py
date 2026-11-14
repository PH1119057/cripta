"""#470 R11 immutable original cursor-bound publication read regression."""
from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

import bybit_workbench.mayak.pinned_original_publication_page as pinned
from bybit_workbench.market_identity import BYBIT_PUBLIC_MARKET_SOURCE_ID
from bybit_workbench.mayak.optional_original_event_parity import (
    R11OptionalMayakBatch,
    R11OptionalMayakCursor,
)
from bybit_workbench.mayak.pinned_original_publication_page import (
    R11PinnedMayakPublicationUnverified,
    read_r11_pinned_mayak_optional_page,
)

SESSION = "a" * 32
EPOCH = "b" * 32
RELEASE = "c" * 40
DIGEST = "d" * 64
AT = datetime(2026, 11, 14, 16, 0, tzinfo=UTC)


class Row:
    def __init__(self, result: tuple[object, ...] | None):
        self.result = result

    def fetchone(self) -> tuple[object, ...] | None:
        return self.result


class DB:
    def __init__(self) -> None:
        self.isolation = "repeatable read"
        self.read_only = "on"
        self.refs: dict[str, dict[str, object]] = {
            "SPOT": {
                "source_session_id": SESSION,
                "transport_epoch_id": EPOCH,
                "product_category": "SPOT",
                "producer_release_commit": RELEASE,
                "source_frame_number": 9,
                "frame_content_sha256": DIGEST,
                "received_at": (AT - timedelta(milliseconds=5)).isoformat(),
            },
        }
        self.coverage: dict[str, str] = {
            "SPOT": "PHYSICAL_FRAME_REF_ONLY",
        }
        self.pub = (
            BYBIT_PUBLIC_MARKET_SOURCE_ID, SESSION, RELEASE, AT,
            "WATERMARKS_ONLY", self.refs, self.coverage,
        )

    def execute(self, sql: str, params: object = None) -> Row:
        if sql == "SHOW transaction_isolation":
            return Row((self.isolation,))
        if sql == "SHOW transaction_read_only":
            return Row((self.read_only,))
        assert "FROM analytics.market_publications" in sql
        assert params == (7,)
        return Row(self.pub)


def cursor() -> R11OptionalMayakCursor:
    return R11OptionalMayakCursor(
        source_session_id=SESSION, transport_epoch_id=EPOCH,
        product_category="SPOT", producer_release_commit=RELEASE,
        next_cursor=9, next_digest=DIGEST, books=(),
    )


def test_pinned_publication_binds_original_page_to_same_immutable_cursor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, object]] = []

    def read(_db: object, **kwargs: object) -> object:
        calls.append(kwargs)
        return object()

    def project(_page: object, **_kwargs: object) -> R11OptionalMayakBatch:
        return R11OptionalMayakBatch(
            cursor=cursor(), events=(), has_more_pages=False,
        )

    monkeypatch.setattr(pinned, "read_r11_optional_original_frame_page", read)
    monkeypatch.setattr(pinned, "project_r11_optional_mayak_page", project)
    page = read_r11_pinned_mayak_optional_page(
        DB(), publication_id=7, product_category="SPOT",
    )
    assert page.physical_published_cursor_verified
    assert not page.provider_transport_continuity_verified
    assert not page.seven_symbol_analytical_parity_verified
    assert not page.mayak_ws_retirement_allowed
    assert calls[0]["through_frame"] == 9
    assert calls[0]["transport_epoch_id"] == EPOCH
    assert calls[0]["max_frames"] == 128


@pytest.mark.parametrize("wrong", [
    "read_committed", "read_write", "other_source", "other_release",
    "missing_ref", "wrong_coverage", "other_epoch",
    "future_received", "bad_digest", "wrong_ref_product",
])
def test_bad_publication_or_transport_never_enters_mayak(
    wrong: str,
) -> None:
    db = DB()
    if wrong == "read_committed":
        db.isolation = "read committed"
    elif wrong == "read_write":
        db.read_only = "off"
    elif wrong == "other_source":
        db.pub = ("other-market", *db.pub[1:])
    elif wrong == "other_release":
        db.pub = (*db.pub[:2], "f" * 40, *db.pub[3:])
    elif wrong == "missing_ref":
        db.refs.clear()
    elif wrong == "wrong_coverage":
        db.coverage["SPOT"] = "TRANSPORT_CONTINUOUS"
    elif wrong == "other_epoch":
        db.refs["SPOT"]["transport_epoch_id"] = "not-epoch"
    elif wrong == "future_received":
        db.refs["SPOT"]["received_at"] = (AT + timedelta(seconds=1)).isoformat()
    elif wrong == "bad_digest":
        db.refs["SPOT"]["frame_content_sha256"] = "invalid"
    elif wrong == "wrong_ref_product":
        db.refs["SPOT"]["product_category"] = "LINEAR"
    with pytest.raises(R11PinnedMayakPublicationUnverified):
        read_r11_pinned_mayak_optional_page(
            db, publication_id=7, product_category="SPOT",
        )


def test_transport_epoch_restart_rejects_old_checkpoint() -> None:
    db = DB()
    old = replace(cursor(), transport_epoch_id="e" * 32)
    with pytest.raises(R11PinnedMayakPublicationUnverified,
                       match="PREVIOUS_CURSOR_OR_EPOCH_DRIFT"):
        read_r11_pinned_mayak_optional_page(
            db, publication_id=7, product_category="SPOT",
            previous=old,
        )


def test_incomplete_page_cannot_be_falsely_read_as_pinned_completion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        pinned, "read_r11_optional_original_frame_page",
        lambda *_a, **_kw: object(),
    )
    monkeypatch.setattr(
        pinned, "project_r11_optional_mayak_page",
        lambda *_a, **_kw: R11OptionalMayakBatch(
            cursor=replace(cursor(), next_cursor=8), events=(),
            has_more_pages=False,
        ),
    )
    with pytest.raises(R11PinnedMayakPublicationUnverified,
                       match="UNREACHED_PUBLICATION_CURSOR"):
        read_r11_pinned_mayak_optional_page(
            DB(), publication_id=7, product_category="SPOT",
        )
