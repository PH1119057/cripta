#!/usr/bin/env python3
from __future__ import annotations

import csv
import json
import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import psycopg

DSN = os.environ.get("CRIPTA_DSN", "dbname=cripta user=cripta host=/var/run/postgresql")
REPORT_ROOT = Path(os.environ.get("MAYAK_REPORT_ROOT", "/srv/cripta-share/reports"))


def main() -> None:
    days = int(sys.argv[1]) if len(sys.argv) > 1 else 1
    if days not in (1, 7):
        raise SystemExit("Период отчёта: только 1 или 7 суток")
    until, since = datetime.now(UTC), datetime.now(UTC) - timedelta(days=days)
    target = REPORT_ROOT / f"mayak_v2_{days}d_{until:%Y%m%d_%H%M%S}"
    target.mkdir(parents=True, exist_ok=False)
    with psycopg.connect(DSN) as db:
        states = db.execute(
            """SELECT state,count(*),avg(confidence),min(observed_at),max(observed_at)
            FROM mayak_v2.snapshots WHERE observed_at BETWEEN %s AND %s
            GROUP BY state ORDER BY count(*) DESC""",
            (since, until),
        ).fetchall()
        coins = db.execute(
            """SELECT symbol,count(*),avg(return_5m_pct),avg(spot_net_usd),
            avg(derivatives_net_usd),avg(open_interest_change_pct),avg(funding_rate),
            avg(spot_bid_change_pct),avg(derivatives_bid_change_pct)
            FROM mayak_v2.coin_minutes WHERE observed_at BETWEEN %s AND %s
            GROUP BY symbol ORDER BY symbol""",
            (since, until),
        ).fetchall()
        events = db.execute(
            """SELECT occurred_at,event_type,reference_id,symbol,side,snapshot_id,payload
            FROM mayak_v2.events WHERE occurred_at BETWEEN %s AND %s ORDER BY occurred_at""",
            (since, until),
        ).fetchall()
        regular_minutes = [
            row[0]
            for row in db.execute(
                """SELECT regular_minute FROM mayak_v2.snapshots
                WHERE snapshot_kind='REGULAR' AND regular_minute BETWEEN %s AND %s
                ORDER BY regular_minute""",
                (since, until),
            ).fetchall()
        ]
        quality_row = db.execute(
            """SELECT count(*) AS total,
                      count(*) FILTER (WHERE spot_net_usd IS NOT NULL) AS spot_valid,
                      count(*) FILTER (WHERE derivatives_net_usd IS NOT NULL) AS derivatives_valid,
                      count(*) FILTER (WHERE open_interest IS NOT NULL) AS oi_valid
               FROM mayak_v2.coin_minutes
               WHERE observed_at BETWEEN %s AND %s""",
            (since, until),
        ).fetchone()
        liquidation_quality = db.execute(
            """SELECT coalesce(payload->'liquidations'->>'status','NO_DATA') AS status,
                      count(*)
               FROM mayak_v2.snapshots
               WHERE snapshot_kind='REGULAR' AND observed_at BETWEEN %s AND %s
               GROUP BY 1 ORDER BY 2 DESC""",
            (since, until),
        ).fetchall()
        continuity_gaps = db.execute(
            """SELECT source,scope,count(*),sum(duration_seconds),max(duration_seconds),
                      min(gap_started_at),max(gap_ended_at)
               FROM mayak_v2.continuity_gaps
               WHERE gap_started_at <= %s AND gap_ended_at >= %s
               GROUP BY source,scope ORDER BY source,scope""",
            (until, since),
        ).fetchall()
    continuity = _continuity(regular_minutes)
    total_quality_rows = int(quality_row[0] or 0)
    source_coverage = {
        "rows": total_quality_rows,
        "spot_5m_flow_pct": _pct(quality_row[1], total_quality_rows),
        "derivatives_5m_flow_pct": _pct(quality_row[2], total_quality_rows),
        "open_interest_pct": _pct(quality_row[3], total_quality_rows),
    }
    summary = {
        "период_суток": days,
        "начало": since.isoformat(),
        "окончание": until.isoformat(),
        "снимков": sum(row[1] for row in states),
        "состояния": [
            {
                "состояние": row[0],
                "минут": row[1],
                "средняя_уверенность": row[2],
                "первое": row[3],
                "последнее": row[4],
            }
            for row in states
        ],
        "связанных_торговых_событий": len(events),
        "непрерывность_regular_snapshot": continuity,
        "покрытие_источников": source_coverage,
        "качество_ликвидаций": [
            {"status": row[0], "snapshots": row[1]} for row in liquidation_quality
        ],
        "durable_continuity_gaps": [
            {
                "source": row[0],
                "scope": row[1],
                "gaps": row[2],
                "duration_seconds_total": row[3],
                "duration_seconds_max": row[4],
                "first_gap_started_at": row[5],
                "last_gap_ended_at": row[6],
            }
            for row in continuity_gaps
        ],
        "оговорка": (
            "Маяк наблюдает и не изменяет торговые решения. "
            "Пропущенные buckets/events не синтезируются."
        ),
    }
    (target / "СВОДКА.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    write_csv(
        target / "МОНЕТЫ.csv",
        (
            "монета",
            "минут",
            "среднее_движение_5м",
            "спотовый_поток",
            "срочный_поток",
            "изменение_открытого_интереса",
            "финансирование",
            "изменение_покупательской_ликвидности_спот",
            "изменение_покупательской_ликвидности_срочный",
        ),
        coins,
    )
    write_csv(
        target / "СОБЫТИЯ.csv",
        (
            "время",
            "тип",
            "идентификатор",
            "монета",
            "сторона",
            "снимок",
            "данные",
        ),
        events,
    )
    print(target)


def _pct(valid: object, total: int) -> float | None:
    if total <= 0:
        return None
    return round(int(valid or 0) / total * 100, 6)


def _continuity(minutes: list[datetime]) -> dict[str, object]:
    if not minutes:
        return {
            "first_minute": None,
            "last_minute": None,
            "expected_snapshots": 0,
            "actual_snapshots": 0,
            "missing_snapshots": 0,
            "max_gap_minutes": 0,
            "coverage_pct": None,
        }
    unique = sorted(set(minutes))
    expected = int((unique[-1] - unique[0]).total_seconds() // 60) + 1
    gaps = [
        int((right - left).total_seconds() // 60)
        for left, right in zip(unique, unique[1:], strict=False)
    ]
    actual = len(unique)
    return {
        "first_minute": unique[0].isoformat(),
        "last_minute": unique[-1].isoformat(),
        "expected_snapshots": expected,
        "actual_snapshots": actual,
        "missing_snapshots": max(0, expected - actual),
        "max_gap_minutes": max(gaps, default=0),
        "coverage_pct": round(actual / expected * 100, 6) if expected else None,
    }


def write_csv(path: Path, headings: tuple[str, ...], rows: list[tuple[object, ...]]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(headings)
        writer.writerows(rows)


if __name__ == "__main__":
    main()
