"""R07 source-publication proof for an account-scoped REAL Entry request.

One reusable Consumer implementation serves arbitrary registered TradingAccounts.
This read-only gate proves a P90 request's signal came from the R06 atomic,
original-native, publication-pinned observer epoch. It grants no LIVE rights,
does not generate requests and never sends Exchange commands. Legacy non-P90
requests keep their existing independent admission/authorization contract.
"""
from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any

from bybit_workbench.market_identity import (
    BYBIT_PUBLIC_MARKET_SOURCE_ID,
    bybit_instrument_id,
)

from .p90l90_real_contract_v3 import ALLOWED_SYMBOLS, VERSION

_SHA40 = re.compile(r"^[0-9a-f]{40}$")
_SHA64 = re.compile(r"^[0-9a-f]{64}$")
_MAX_SIGNAL_AGE = timedelta(seconds=5)  # Existing REAL v3 EntryPlan request limit.


class R07PublicationProvenanceUnavailable(RuntimeError):
    """Published original source/decision checkpoint is missing or inconsistent."""


_SQL = """
SELECT o.publication_id, o.source_session_id, o.frame_number, o.frame_index,
       o.native_exec_id, o.event_at, o.symbol, o.direction,
       p.market_source_id, p.source_session_id AS publication_session_id,
       p.producer_release_commit, p.published_at, p.as_of_second,
       p.publication_status,
       s.state AS source_session_state,
       n.market_source_id AS event_market_source_id,
       n.instrument_id, n.external_symbol, n.exec_id,
       n.event_at AS original_event_at, n.accepted_at,
       cp.frame_number AS checkpoint_frame_number,
       cp.frame_index AS checkpoint_frame_index,
       cp.last_publication_id, cp.bootstrap_publication_id,
       cp.entry_plan_fingerprint, cp.exit_plan_fingerprint,
       cp.producer_release_commit AS checkpoint_producer_release_commit,
       cp.consumer_release_commit
  FROM strategy_entry.p90_r06_publication_observations o
  JOIN analytics.market_publications p
    ON p.publication_id=o.publication_id
  JOIN analytics.native_public_trade_source_sessions s
    ON s.source_session_id=o.source_session_id
   AND s.market_source_id=p.market_source_id
  JOIN analytics.native_public_trade_source_events n
    ON n.source_session_id=o.source_session_id
   AND n.frame_number=o.frame_number
   AND n.frame_index=o.frame_index
  JOIN strategy_entry.p90_scoped_native_consumer_checkpoints cp
    ON cp.strategy_id=o.strategy_id
   AND cp.strategy_version=o.strategy_version
   AND cp.source_session_id=o.source_session_id
 WHERE o.strategy_id=%s AND o.strategy_version=%s
   AND o.signal_id=%s AND o.symbol=%s AND o.direction=%s
 ORDER BY o.publication_id, o.source_session_id
 LIMIT 2
"""


def require_r07_publication_provenance(
    connection: Any,
    request: Mapping[str, object],
) -> int | None:
    """Return exact immutable publication ID, or block before command creation.

    Non-P90 strategy requests are unaffected. A P90 strategy cannot bypass
    the new proof by supplying an unexpected StrategyVersion. Account/connector,
    funding, position slot, signed Exchange and OWNER ARM are *additional*
    mandatory gates implemented by the existing universal Consumer.
    """
    strategy_id = str(request.get("strategy_id") or "")
    version = str(request.get("strategy_version") or "")
    if strategy_id not in ALLOWED_SYMBOLS:
        return None
    if version != VERSION:
        raise R07PublicationProvenanceUnavailable("R07_P90_STRATEGY_VERSION_MISMATCH")

    symbol = str(request.get("symbol") or "")
    direction = str(request.get("direction") or "")
    signal_id = str(request.get("signal_id") or "")
    requested_at = request.get("requested_at")
    entry_plan_fingerprint = request.get("entry_plan_fingerprint")
    exit_plan_fingerprint = request.get("exit_plan_fingerprint")
    if (
        symbol not in ALLOWED_SYMBOLS[strategy_id]
        or direction not in {"LONG", "SHORT"}
        or not signal_id
        or not isinstance(requested_at, datetime)
        or requested_at.tzinfo is None
    ):
        raise R07PublicationProvenanceUnavailable("R07_REQUEST_SOURCE_IDENTITY_INVALID")

    if (
        not isinstance(entry_plan_fingerprint, str)
        or _SHA64.fullmatch(entry_plan_fingerprint) is None
        or not isinstance(exit_plan_fingerprint, str)
        or _SHA64.fullmatch(exit_plan_fingerprint) is None
    ):
        raise R07PublicationProvenanceUnavailable("R07_REQUEST_PLAN_IDENTITY_INVALID")

    observations = connection.execute(
        _SQL, (strategy_id, version, signal_id, symbol, direction)
    ).fetchall()
    if len(observations) != 1:
        raise R07PublicationProvenanceUnavailable("R07_SOURCE_SIGNAL_MISSING_OR_AMBIGUOUS")
    proof = observations[0]
    if not isinstance(proof, Mapping):
        raise R07PublicationProvenanceUnavailable("R07_SOURCE_PROOF_ROW_INVALID")

    event_at = proof.get("event_at")
    original_at = proof.get("original_event_at")
    published_at = proof.get("published_at")
    accepted_at = proof.get("accepted_at")
    as_of = proof.get("as_of_second")
    # Explicit narrowing is required: a tuple/generator validity check does
    # not refine each distinct nullable field for static type checking.
    if (
        not isinstance(event_at, datetime) or event_at.tzinfo is None
        or not isinstance(original_at, datetime) or original_at.tzinfo is None
        or not isinstance(published_at, datetime) or published_at.tzinfo is None
        or not isinstance(accepted_at, datetime) or accepted_at.tzinfo is None
        or not isinstance(as_of, datetime) or as_of.tzinfo is None
    ):
        raise R07PublicationProvenanceUnavailable("R07_SOURCE_PROOF_TIME_INVALID")
    request_utc = requested_at.astimezone(UTC)
    event_utc = event_at.astimezone(UTC)
    if not (
        timedelta(0) <= request_utc - event_utc <= _MAX_SIGNAL_AGE
        and accepted_at <= published_at <= requested_at
        and as_of <= published_at
        and original_at == event_at
    ):
        raise R07PublicationProvenanceUnavailable("R07_SOURCE_PROOF_TIME_OR_FRESHNESS_MISMATCH")

    try:
        publication_id = proof["publication_id"]
        frame = int(proof["frame_number"])
        index = int(proof["frame_index"])
        last_frame = int(proof["checkpoint_frame_number"])
        last_index = int(proof["checkpoint_frame_index"])
        bootstrap_id = proof["bootstrap_publication_id"]
        last_publication_id = proof["last_publication_id"]
        instrument_id = bybit_instrument_id("linear", symbol)
    except (KeyError, TypeError, ValueError) as exc:
        raise R07PublicationProvenanceUnavailable("R07_SOURCE_PROOF_STRUCTURE_INVALID") from exc

    if (
        type(publication_id) is not int or publication_id < 1
        or type(bootstrap_id) is not int or type(last_publication_id) is not int
        or not (1 <= bootstrap_id <= publication_id <= last_publication_id)
        or frame < 0 or index < 0 or (last_frame, last_index) < (frame, index)
        or proof.get("entry_plan_fingerprint") != entry_plan_fingerprint
        or proof.get("exit_plan_fingerprint") != exit_plan_fingerprint
        or proof.get("checkpoint_producer_release_commit") != proof.get("producer_release_commit")
        or proof.get("publication_session_id") != proof.get("source_session_id")
        or proof.get("source_session_state") != "ACTIVE"
        or proof.get("market_source_id") != BYBIT_PUBLIC_MARKET_SOURCE_ID
        or proof.get("event_market_source_id") != BYBIT_PUBLIC_MARKET_SOURCE_ID
        or proof.get("instrument_id") != instrument_id
        or proof.get("external_symbol") != symbol
        or proof.get("exec_id") != proof.get("native_exec_id")
        or proof.get("publication_status") != "WATERMARKS_ONLY"
        or not isinstance(proof.get("producer_release_commit"), str)
        or _SHA40.fullmatch(proof["producer_release_commit"]) is None
        or not isinstance(proof.get("consumer_release_commit"), str)
        or _SHA40.fullmatch(proof["consumer_release_commit"]) is None
    ):
        raise R07PublicationProvenanceUnavailable("R07_SOURCE_PROOF_INVARIANT_BROKEN")
    return publication_id
