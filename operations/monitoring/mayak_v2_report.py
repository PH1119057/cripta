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
        "оговорка": "Маяк наблюдает и не изменяет торговые решения.",
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


def write_csv(path: Path, headings: tuple[str, ...], rows: list[tuple[object, ...]]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(headings)
        writer.writerows(rows)


if __name__ == "__main__":
    main()
