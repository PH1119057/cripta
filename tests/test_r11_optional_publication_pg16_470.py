"""#470 R11 additive originals and immutable publication schema in disposable PG16.

This test NEVER touches production; it is skipped without an explicitly
supplied dedicated disposable PostgreSQL DSN.
"""
from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path

import psycopg
import pytest
from psycopg.types.json import Jsonb

ROOT = Path(__file__).resolve().parents[1]
ORIGINAL = ROOT / "operations/sql/20261114_r11_optional_original_public_event_frames.sql"
PUBLICATION = ROOT / "operations/sql/20261114_r11_optional_publication_frame_refs.sql"
BASE = ROOT / "operations/sql/20261110_market_source_publication.sql"
DSN = os.environ.get("CRIPTA_R11_DISPOSABLE_PG_DSN", "")


@pytest.fixture(scope="module")
def pg() -> psycopg.Connection:
    if not DSN:
        pytest.skip("Requires explicitly disposable PostgreSQL16 DSN")
    connection = psycopg.connect(DSN, autocommit=True)
    if int(connection.execute("SHOW server_version_num").fetchone()[0]) // 10000 != 16:
        pytest.skip("Exact PostgreSQL 16 disposable fixture required")
    connection.execute("CREATE SCHEMA IF NOT EXISTS identity")
    connection.execute("CREATE SCHEMA IF NOT EXISTS analytics")
    connection.execute(
        """DO $$
           BEGIN
             IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='cripta')
             THEN CREATE ROLE cripta LOGIN;
             END IF;
           END $$"""
    )
    connection.execute("GRANT USAGE ON SCHEMA analytics,identity TO cripta")
    connection.execute(
        """CREATE TABLE IF NOT EXISTS identity.market_sources (
             market_source_id text PRIMARY KEY, venue_id text NOT NULL,
             UNIQUE(market_source_id,venue_id)
           )"""
    )
    connection.execute(
        """CREATE TABLE IF NOT EXISTS identity.instruments (
             instrument_id text NOT NULL, venue_id text NOT NULL,
             product_category text NOT NULL, external_symbol text NOT NULL,
             PRIMARY KEY (
               instrument_id,venue_id,product_category,external_symbol
             )
           )"""
    )
    connection.execute(
        """CREATE TABLE IF NOT EXISTS
           analytics.native_public_trade_source_sessions (
             source_session_id text PRIMARY KEY
           )"""
    )
    connection.execute(
        """INSERT INTO identity.market_sources VALUES
           ('ms-bybit-mainnet-public-v1','venue-bybit')
           ON CONFLICT DO NOTHING"""
    )
    connection.execute(
        """INSERT INTO identity.instruments VALUES
           ('inst-bybit-spot-btcusdt','venue-bybit','SPOT','BTCUSDT')
           ON CONFLICT DO NOTHING"""
    )
    connection.execute(
        """INSERT INTO analytics.native_public_trade_source_sessions
           VALUES ('aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa')
           ON CONFLICT DO NOTHING"""
    )
    for script in (BASE, ORIGINAL, PUBLICATION):
        connection.execute(script.read_text(encoding="utf-8"))
    try:
        yield connection
    finally:
        connection.close()


def test_r11_schema_immutable_original_frames_and_replay_index(
    pg: psycopg.Connection,
) -> None:
    assert pg.execute(
        """SELECT to_regclass(
            'analytics.public_source_original_event_frames')"""
    ).fetchone()[0] is not None
    columns = {
        row[0] for row in pg.execute(
            """SELECT column_name FROM information_schema.columns
               WHERE table_schema='analytics'
                 AND table_name='market_publications'"""
        ).fetchall()
    }
    assert {
        "original_optional_frame_refs", "original_optional_frame_coverage",
    } <= columns
    indexes = {
        row[0] for row in pg.execute(
            """SELECT indexname FROM pg_indexes
               WHERE schemaname='analytics'
                 AND tablename='public_source_original_event_frames'"""
        ).fetchall()
    }
    assert "public_source_original_event_frames_replay_idx" in indexes


def test_r11_exact_runtime_grants_never_allow_original_mutation(
    pg: psycopg.Connection,
) -> None:
    original = "analytics.public_source_original_event_frames"
    publication = "analytics.market_publications"
    grants = {
        item: tuple(bool(x) for x in pg.execute(
            """SELECT has_table_privilege('cripta',%s,'SELECT'),
                      has_table_privilege('cripta',%s,'INSERT'),
                      has_table_privilege('cripta',%s,'UPDATE'),
                      has_table_privilege('cripta',%s,'DELETE'),
                      has_table_privilege('cripta',%s,'TRUNCATE')""",
            (item,) * 5,
        ).fetchone())
        for item in (original, publication)
    }
    assert grants[original] == (True, True, False, False, False)
    assert grants[publication] == (True, True, False, False, False)


def test_r11_exact_btc_original_row_and_pub_refs_are_idempotent(
    pg: psycopg.Connection,
) -> None:
    t = datetime(2026, 11, 14, 16, 0, tzinfo=UTC)
    session = "a" * 32
    pg.execute(
        """INSERT INTO analytics.public_source_original_event_frames
           (source_session_id,transport_epoch_id,market_source_id,venue_id,
            instrument_id,product_category,external_symbol,fact_family,
            source_topic,source_frame_number,frame_type,provider_frame_at,
            source_event_first_at,source_event_last_at,received_at,
            provider_frame,decoded_original_facts,frame_content_sha256,
            producer_release_commit)
           VALUES (%s,%s,%s,'venue-bybit',%s,'SPOT','BTCUSDT',
                   'SPOT_TRADE','publicTrade.BTCUSDT',1,'snapshot',
                   %s,%s,%s,%s,%s,%s,%s,%s)
           ON CONFLICT DO NOTHING""",
        (
            session, "b" * 32, "ms-bybit-mainnet-public-v1",
            "inst-bybit-spot-btcusdt", t, t, t, t,
            Jsonb({"topic": "publicTrade.BTCUSDT", "ts": 1794672000000}),
            Jsonb([{"identity": "original-not-compact"}]),
            "c" * 64, "d" * 40,
        ),
    )
    pg.execute(
        """INSERT INTO analytics.market_publications
           (market_source_id,source_session_id,producer_release_commit,
            as_of_second,native_watermarks,closed_candle_watermarks,
            field_coverage,publication_status,original_optional_frame_refs,
            original_optional_frame_coverage)
           VALUES (%s,%s,%s,%s,%s,%s,%s,'WATERMARKS_ONLY',%s,%s)
           ON CONFLICT DO NOTHING""",
        (
            "ms-bybit-mainnet-public-v1", session, "d" * 40, t,
            Jsonb({}), Jsonb({}), Jsonb({}),
            Jsonb({"SPOT": {
                "source_frame_number": 1,
                "frame_content_sha256": "c" * 64,
            }}),
            Jsonb({"SPOT": "PHYSICAL_FRAME_REF_ONLY"}),
        ),
    )
    before = pg.execute(
        """SELECT frame_content_sha256
             FROM analytics.public_source_original_event_frames
            WHERE source_session_id=%s AND source_frame_number=1""",
        (session,),
    ).fetchone()
    assert before == ("c" * 64,)
    row = pg.execute(
        """SELECT original_optional_frame_refs,
                  original_optional_frame_coverage
             FROM analytics.market_publications
            WHERE source_session_id=%s AND as_of_second=%s""",
        (session, t),
    ).fetchone()
    assert row[0]["SPOT"]["frame_content_sha256"] == "c" * 64
    assert row[1] == {"SPOT": "PHYSICAL_FRAME_REF_ONLY"}
    for script in (ORIGINAL, PUBLICATION):
        pg.execute(script.read_text(encoding="utf-8"))
    assert pg.execute(
        """SELECT count(*)
           FROM analytics.public_source_original_event_frames
           WHERE source_session_id=%s""", (session,),
    ).fetchone() == (1,)


def test_r11_prod_role_update_and_delete_denied(
    pg: psycopg.Connection,
) -> None:
    pg.execute("SET ROLE cripta")
    try:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            pg.execute(
                """UPDATE analytics.public_source_original_event_frames
                   SET frame_content_sha256='f'"""
            )
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            pg.execute("DELETE FROM analytics.market_publications")
    finally:
        pg.execute("RESET ROLE")


def test_r11_product_topic_mismatch_is_physical_check_failure(
    pg: psycopg.Connection,
) -> None:
    from psycopg.errors import CheckViolation

    t = datetime(2026, 11, 14, 16, 0, tzinfo=UTC)
    with pytest.raises(CheckViolation):
        pg.execute(
            """INSERT INTO analytics.public_source_original_event_frames
               (source_session_id,transport_epoch_id,market_source_id,
                instrument_id,product_category,external_symbol,fact_family,
                source_topic,source_frame_number,frame_type,provider_frame_at,
                source_event_first_at,source_event_last_at,received_at,
                provider_frame,decoded_original_facts,frame_content_sha256,
                producer_release_commit)
               VALUES (%s,%s,%s,%s,'SPOT','BTCUSDT','LIQUIDATION',
                       'allLiquidation.BTCUSDT',2,'snapshot',
                       %s,%s,%s,%s,%s,%s,%s,%s)""",
            (
                "a" * 32, "b" * 32, "ms-bybit-mainnet-public-v1",
                "inst-bybit-spot-btcusdt", t, t, t, t,
                Jsonb({}), Jsonb([{}]), "c" * 64, "d" * 40,
            ),
        )
