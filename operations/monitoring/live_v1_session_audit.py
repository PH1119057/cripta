from __future__ import annotations

import csv
import json
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import psycopg
from psycopg.rows import dict_row

POLICY = "m3_full_live_v1"


def _json(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    try:
        return json.loads(value or "{}")
    except (TypeError, ValueError):
        return {}


def _session_start(connection: psycopg.Connection[Any]) -> int:
    row = connection.execute(
        """SELECT changed_at_epoch_ms FROM runtime.trade_settings_history
           WHERE new_settings->>'entry_policy'=%s
           ORDER BY changed_at_epoch_ms DESC LIMIT 1""",
        (POLICY,),
    ).fetchone()
    if row:
        return int(row["changed_at_epoch_ms"])
    row = connection.execute(
        """SELECT min(decided_at_epoch_ms) AS started
           FROM runtime.entry_decisions WHERE entry_policy=%s""",
        (POLICY,),
    ).fetchone()
    if not row or row["started"] is None:
        raise RuntimeError("В PostgreSQL не найдено начало FULL LIVE-сессии")
    return int(row["started"])


def build(
    connection: psycopg.Connection[Any], cutoff_ms: int
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    started_ms = _session_start(connection)
    decisions = connection.execute(
        """SELECT * FROM runtime.entry_decisions
           WHERE entry_policy=%s AND decided_at_epoch_ms BETWEEN %s AND %s
           ORDER BY decided_at_epoch_ms""",
        (POLICY, started_ms, cutoff_ms),
    ).fetchall()
    contexts = connection.execute(
        """SELECT * FROM runtime.m3_consumed_context
           WHERE strategy_decision_at BETWEEN to_timestamp(%s/1000.0) AND to_timestamp(%s/1000.0)
           ORDER BY strategy_decision_at""",
        (started_ms, cutoff_ms),
    ).fetchall()
    commands = connection.execute(
        """SELECT * FROM runtime.trade_commands
           WHERE requested_at_epoch_ms BETWEEN %s AND %s ORDER BY requested_at_epoch_ms""",
        (started_ms, cutoff_ms),
    ).fetchall()
    executions = connection.execute(
        """SELECT * FROM runtime.executions WHERE exec_time_ms BETWEEN %s AND %s
           ORDER BY exec_time_ms""",
        (started_ms, cutoff_ms),
    ).fetchall()
    context_by_signal = {str(row["signal_id"]): row for row in contexts}
    status_counts = Counter(str(row["dispatcher_status"]) for row in contexts)
    decision_counts = Counter(str(row["decision"]) for row in decisions)
    reason_counts = Counter(
        str(row["reason"]) for row in decisions if str(row["decision"]) != "разрешён"
    )
    entry_commands = [row for row in commands if row["command_type"] == "entry"]
    close_commands = [row for row in commands if row["command_type"] == "close"]
    fills = [row for row in executions if DecimalSafe(row["exec_qty"]) > 0]
    funnel_rows: list[dict[str, Any]] = []
    for row in decisions:
        context = context_by_signal.get(str(row["signal_id"]))
        details = _json(row["details_json"])
        funnel_rows.append(
            {
                "время_UTC": datetime.fromtimestamp(
                    row["decided_at_epoch_ms"] / 1000, UTC
                ).isoformat(),
                "signal_id": row["signal_id"],
                "монета": row["symbol"],
                "направление": row["direction"],
                "решение": row["decision"],
                "причина": row["reason"],
                "статус_Диспетчера": None if context is None else context["dispatcher_status"],
                "assessment_id": None if context is None else context["assessment_id"],
                "duplicate_or_occupied": bool(
                    details.get("occupied") or "позици" in str(row["reason"]).lower()
                ),
                "embargo": "embargo" in json.dumps(details, ensure_ascii=False).lower(),
            }
        )
    anatomy: list[dict[str, Any]] = []
    for command in entry_commands:
        payload = _json(command["payload_json"])
        result = _json(command["result_json"])
        order_link_id = str(result.get("orderLinkId") or payload.get("order_link_id") or "")
        related = [
            row
            for row in executions
            if order_link_id and str(row["order_link_id"]) == order_link_id
        ]
        anatomy.append(
            {
                "command_id": command["command_id"],
                "монета": command["symbol"],
                "состояние_команды": command["state"],
                "orderLinkId": order_link_id or None,
                "orderId": result.get("orderId"),
                "исполнений": len(related),
                "объём_исполнения": str(sum(DecimalSafe(row["exec_qty"]) for row in related)),
                "комиссия_входа": str(sum(DecimalSafe(row["exec_fee"]) for row in related)),
                "completed_не_означает_filled": command["state"] == "completed" and not related,
            }
        )
    completeness = "PARTIAL"
    report = {
        "schema": "live-v1-session-audit-1.1",
        "session_start_source": "runtime.trade_settings_history:new_settings.entry_policy",
        "session_started_at": datetime.fromtimestamp(started_ms / 1000, UTC).isoformat(),
        "audit_cutoff": datetime.fromtimestamp(cutoff_ms / 1000, UTC).isoformat(),
        "data_completeness": completeness,
        "entry_funnel": {
            "unique_core_signals": len({str(row["signal_id"]) for row in decisions}),
            "valid_consumed_context": len(contexts),
            "dispatcher_statuses": dict(status_counts),
            "decisions": dict(decision_counts),
            "top_block_reasons": dict(reason_counts.most_common(20)),
            "entry_commands": len(entry_commands),
            "entry_commands_completed": sum(row["state"] == "completed" for row in entry_commands),
            "entry_commands_failed": sum(row["state"] == "failed" for row in entry_commands),
            "execution_rows": len(fills),
        },
        "exit_attribution": {
            "close_commands": len(close_commands),
            "warning_incompatible_candidates": sum(
                _json(row["payload_json"]).get("supervisor_state") == "WARNING"
                for row in close_commands
            ),
            "broken_incompatible_candidates": sum(
                _json(row["payload_json"]).get("supervisor_state") == "BROKEN"
                for row in close_commands
            ),
        },
        "missing_layers": [
            "точная история состояния ордера New/Partial/TTL не нормализована",
            "фактический closed-PnL Bybit не сохранён отдельной PostgreSQL-таблицей",
            "полная причинная цепочка Analyst ещё не нормализована",
        ],
    }
    return report, funnel_rows, anatomy


def DecimalSafe(value: Any):
    from decimal import Decimal

    try:
        return Decimal(str(value or 0))
    except Exception:
        return Decimal(0)


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fields = list(rows[0]) if rows else ["нет_данных"]
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    cutoff_ms = int(datetime.now(UTC).timestamp() * 1000)
    stamp = datetime.fromtimestamp(cutoff_ms / 1000, UTC).strftime("%Y%m%d_%H%M%S")
    root = Path("/srv/cripta/reports")
    with psycopg.connect(
        "dbname=cripta user=cripta host=/var/run/postgresql", row_factory=dict_row
    ) as connection:
        report, funnel, anatomy = build(connection, cutoff_ms)
    json_path = root / f"live_v1_session_audit_{stamp}.json"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    write_csv(root / f"live_v1_entry_funnel_{stamp}.csv", funnel)
    write_csv(root / f"live_v1_trade_anatomy_{stamp}.csv", anatomy)
    summary = report["entry_funnel"]
    md = "\n".join(
        [
            "# Аудит текущей FULL LIVE-сессии",
            "",
            f"Период: {report['session_started_at']} — {report['audit_cutoff']}",
            f"Полнота: **{report['data_completeness']}**",
            "",
            f"- Уникальных M3-сигналов: {summary['unique_core_signals']}",
            f"- Причинных контекстов Диспетчера: {summary['valid_consumed_context']}",
            f"- Решения: {summary['decisions']}",
            "- Команды Entry: "
            f"{summary['entry_commands']} "
            f"(completed={summary['entry_commands_completed']}, "
            f"failed={summary['entry_commands_failed']})",
            f"- Строк исполнений биржи: {summary['execution_rows']}",
            "",
            "## Неполные слои",
            *[f"- {item}" for item in report["missing_layers"]],
            "",
            "Аудит ничего не менял в торговых правилах или настройках.",
        ]
    )
    (root / f"live_v1_session_audit_{stamp}.md").write_text(md, encoding="utf-8")
    print(json_path)


if __name__ == "__main__":
    main()
