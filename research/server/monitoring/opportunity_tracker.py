from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime

import psycopg

from bybit_workbench.market_data.r13_analytics_source import (
    R13AnalyticsSourceUnavailable,
    R13SourceEpoch,
    fence_active_source_epoch,
    read_active_source_epoch,
    read_opportunity_trade_batch,
    require_opportunity_symbol,
)

LEVELS = (-3.0, -1.1, -1.0, -0.2, -0.1, 0.1, 0.5, 0.7, 1.0, 1.1)


@dataclass
class Opportunity:
    signal_id: str
    symbol: str
    direction: str
    signal_ms: int
    price: float
    horizon: int
    favorable: float = 0
    adverse: float = 0
    hits: dict[str, int] = field(default_factory=dict)
    samples: int = 0
    last_price: float | None = None
    dirty: bool = False

    @property
    def signal_at(self) -> datetime:
        return datetime.fromtimestamp(self.signal_ms / 1000, UTC)

    def observe(self, trade_ms: int, price: float) -> list[tuple[float, int]]:
        if trade_ms < self.signal_ms:
            return []
        move = (
            (price / self.price - 1)
            * 100
            * (1 if self.direction == "long" else -1)
        )
        self.favorable = max(self.favorable, move)
        self.adverse = min(self.adverse, move)
        self.samples += 1
        self.last_price = price
        self.dirty = True
        new_hits = []
        for level in LEVELS:
            key = f"{level:+.1f}"
            reached = move >= level if level > 0 else move <= level
            if reached and key not in self.hits:
                self.hits[key] = trade_ms
                new_hits.append((level, trade_ms))
        return new_hits


def load_active(
    connection: psycopg.Connection,
    active: dict[str, Opportunity],
) -> None:
    rows = connection.execute(
        """SELECT signal_id,symbol,direction,signal_at_epoch_ms,signal_price,
                  horizon_seconds,max_favorable_pct,max_adverse_pct,
                  first_hits_json,samples,last_price
             FROM monitoring.opportunities
            WHERE state='tracking'"""
    ).fetchall()
    ids = set()
    for row in rows:
        ids.add(row[0])
        if row[0] not in active:
            active[row[0]] = Opportunity(
                row[0],
                row[1],
                row[2],
                row[3],
                row[4],
                row[5],
                row[6],
                row[7],
                json.loads(row[8]),
                row[9],
                row[10],
            )
    for signal_id in set(active) - ids:
        active.pop(signal_id, None)


def _checkpoint(
    connection: psycopg.Connection,
    item: Opportunity,
    epoch: R13SourceEpoch,
) -> tuple[int, int, str]:
    row = connection.execute(
        """SELECT source_session_id,producer_release_commit,
                  frame_number,frame_index,source_quality
             FROM monitoring.opportunity_source_checkpoints
            WHERE signal_id=%s
            FOR UPDATE""",
        (item.signal_id,),
    ).fetchone()
    if row is None:
        quality = (
            "CURRENT"
            if item.signal_at >= epoch.subscribed_at
            else "GAP"
        )
        connection.execute(
            """INSERT INTO monitoring.opportunity_source_checkpoints(
                   signal_id,market_source_id,source_session_id,
                   producer_release_commit,frame_number,frame_index,
                   source_quality,last_event_at)
               VALUES(%s,'BYBIT_PUBLIC_V5_MAINNET',%s,%s,0,0,%s,NULL)""",
            (
                item.signal_id,
                epoch.source_session_id,
                epoch.producer_release_commit,
                quality,
            ),
        )
        return 0, 0, quality
    session, producer, frame, index, quality = row
    if session == epoch.source_session_id:
        if producer != epoch.producer_release_commit:
            raise R13AnalyticsSourceUnavailable(
                "R13_ANALYTICS_OPPORTUNITY_PRODUCER_DRIFT"
            )
        return int(frame), int(index), str(quality)

    # A new physical source session is an explicit continuity break for an
    # already-open counterfactual opportunity.  Continue collecting evidence,
    # but never silently promote the result back to complete.
    connection.execute(
        """UPDATE monitoring.opportunity_source_checkpoints
              SET source_session_id=%s,producer_release_commit=%s,
                  frame_number=0,frame_index=0,source_quality='GAP',
                  last_event_at=NULL,updated_at=clock_timestamp()
            WHERE signal_id=%s""",
        (
            epoch.source_session_id,
            epoch.producer_release_commit,
            item.signal_id,
        ),
    )
    return 0, 0, "GAP"


def consume_available(
    connection: psycopg.Connection,
    active: dict[str, Opportunity],
    *,
    limit: int = 512,
) -> int:
    """Consume exact journal rows; each signal's effects+cursor commit together."""
    epoch = read_active_source_epoch(connection)
    processed = 0
    for item in tuple(active.values()):
        with connection.transaction():
            fence_active_source_epoch(connection, epoch=epoch)
            require_opportunity_symbol(
                connection, epoch=epoch, symbol=item.symbol,
            )
            frame, index, quality = _checkpoint(connection, item, epoch)
            events = read_opportunity_trade_batch(
                connection,
                epoch=epoch,
                symbol=item.symbol,
                signal_at=item.signal_at,
                after_frame=frame,
                after_index=index,
                limit=limit,
            )
            for event in events:
                price = float(event.price)
                trade_ms = int(event.event_at.timestamp() * 1000)
                for level, hit_ms in item.observe(trade_ms, price):
                    connection.execute(
                        """INSERT INTO monitoring.opportunity_events(
                               signal_id,at_epoch_ms,event,value_pct,
                               price,details_json)
                           VALUES(%s,%s,'level_first_hit',%s,%s,%s)""",
                        (
                            item.signal_id,
                            hit_ms,
                            level,
                            price,
                            json.dumps(
                                {
                                    "source": "PG_NATIVE_PUBLIC_TRADE",
                                    "source_session_id": (
                                        event.source_session_id
                                    ),
                                    "producer_release_commit": (
                                        event.producer_release_commit
                                    ),
                                    "frame_number": event.frame_number,
                                    "frame_index": event.frame_index,
                                    "exec_id": event.exec_id,
                                },
                                sort_keys=True,
                            ),
                        ),
                    )
            if events:
                last = events[-1]
                connection.execute(
                    """UPDATE monitoring.opportunity_source_checkpoints
                          SET frame_number=%s,frame_index=%s,
                              last_event_at=%s,source_quality=%s,
                              updated_at=clock_timestamp()
                        WHERE signal_id=%s""",
                    (
                        last.frame_number,
                        last.frame_index,
                        last.event_at,
                        quality,
                        item.signal_id,
                    ),
                )
                processed += len(events)
            if item.dirty:
                connection.execute(
                    """UPDATE monitoring.opportunities
                          SET last_price=%s,max_favorable_pct=%s,
                              max_adverse_pct=%s,first_hits_json=%s,samples=%s
                        WHERE signal_id=%s""",
                    (
                        item.last_price,
                        item.favorable,
                        item.adverse,
                        json.dumps(item.hits),
                        item.samples,
                        item.signal_id,
                    ),
                )
                item.dirty = False
    return processed


def finalize_expired(
    connection: psycopg.Connection,
    active: dict[str, Opportunity],
    *,
    now: datetime,
) -> int:
    now_ms = int(now.astimezone(UTC).timestamp() * 1000)
    completed = 0
    for item in list(active.values()):
        if now_ms < item.signal_ms + item.horizon * 1000:
            continue
        with connection.transaction():
            checkpoint = connection.execute(
                """SELECT source_quality,source_session_id,
                          producer_release_commit,frame_number,frame_index
                     FROM monitoring.opportunity_source_checkpoints
                    WHERE signal_id=%s FOR UPDATE""",
                (item.signal_id,),
            ).fetchone()
            if checkpoint is None:
                # Without any proven source epoch this result is not complete.
                continue
            quality, session, producer, frame, index = checkpoint
            state = (
                "completed"
                if quality == "CURRENT"
                else "completed_partial"
            )
            connection.execute(
                """UPDATE monitoring.opportunities
                      SET state=%s,finalized_at_epoch_ms=%s
                    WHERE signal_id=%s AND state='tracking'""",
                (state, now_ms, item.signal_id),
            )
            connection.execute(
                """INSERT INTO monitoring.opportunity_events(
                       signal_id,at_epoch_ms,event,value_pct,price,details_json)
                   VALUES(%s,%s,'horizon_completed',%s,%s,%s)""",
                (
                    item.signal_id,
                    now_ms,
                    item.favorable,
                    item.last_price,
                    json.dumps(
                        {
                            "mfe_pct": item.favorable,
                            "mae_pct": item.adverse,
                            "hits": item.hits,
                            "source_quality": quality,
                            "source_session_id": session,
                            "producer_release_commit": producer,
                            "frame_number": frame,
                            "frame_index": index,
                        },
                        sort_keys=True,
                    ),
                ),
            )
        active.pop(item.signal_id, None)
        completed += 1
    return completed


def main() -> None:
    connection = psycopg.connect(
        "dbname=cripta user=cripta host=/var/run/postgresql "
        "application_name=cripta-opportunity-tracker"
    )
    active: dict[str, Opportunity] = {}
    next_load = 0.0
    try:
        while True:
            now_monotonic = time.monotonic()
            if now_monotonic >= next_load:
                load_active(connection, active)
                connection.commit()
                next_load = now_monotonic + 2.0
            try:
                consume_available(connection, active)
                finalize_expired(
                    connection, active, now=datetime.now(UTC),
                )
            except R13AnalyticsSourceUnavailable:
                connection.rollback()
            time.sleep(0.25)
    finally:
        connection.close()


if __name__ == "__main__":
    main()
