# PASS 5 — стратегии и Mainnet SHADOW

Дата: 14 августа 2026  
Workbench: `0.5.0`  
Автоматические стратегии: `0.2.0`

## Цель

Закрыть неопределённые состояния автоматических стратегий до Windows release и
реального GET-only acceptance: неизвестный результат заявки не должен превращаться в
`FLAT`, рестарт не должен разрешать повторный вход, параметры стратегии не должны
меняться незаметно, а Mainnet Shadow обязан принимать решение только после свежего
снимка биржи.

## Реализовано

1. **`PENDING_UNKNOWN` и обязательное reconciliation.** Неизвестный outcome entry
   сохраняется в state, переживает restore/restart и блокирует следующую свечу.
   Возврат в `FLAT`, `ENTRY_PENDING`, `LONG` или `SHORT` происходит только через
   `on_reconcile()` со свежим контекстом биржи.
2. **Fingerprint параметров.** SHA-256 fingerprint параметров сохраняется в state и
   входит в deterministic intent ID. Изменение параметров уже запущенной или
   восстановленной стратегии отклоняется.
3. **Execution ordering.** Последние execution IDs сохраняются и дедуплицируются;
   новый execution с timestamp старше последнего принятого отклоняется. Курсор
   переживает restore.
4. **Partial fill.** После подтверждённого partial fill стратегия выдаёт только один
   `CancelEntryIntent` на незаполненный остаток и не создаёт повторный cancel на
   duplicate execution.
5. **Неоднозначная свеча Алгоритма 2.** Свеча, одновременно коснувшаяся support и
   resistance zones, не выбирает сторону и отбрасывается.
6. **Mainnet Shadow snapshot-per-candle.** Перед каждой закрытой свечой выполняется
   новый GET-only `read_snapshot`. Snapshot старше последнего принятого отклоняется как
   stale/out-of-order после reconnect.
7. **SHADOW lifecycle.** Виртуальный Mainnet entry получает локальный `SUBMITTED`, а
   виртуальный expiry-cancel — `CANCELLED`; это позволяет state machine корректно
   прожить pending lifecycle без реальной биржевой заявки и без write callback.
8. **Версионная граница.** State форматы подняты до v2, версии стратегий до `0.2.0`,
   Workbench до `0.5.0`. Старый state v1 и старый historical eligibility не могут
   молча смешаться с новой логикой.

## Что намеренно не включено

- Реальный торговый POST.
- Автоматический write-capable strategy provider.
- Full LIVE.
- GET-only acceptance реального аккаунта — это проход 7.
- Micro-Live — отдельный восьмой шаг только после нового явного подтверждения.

## Влияние на historical eligibility

Exact historical gate включает версию Workbench и версию стратегии. Поэтому отчёты,
созданные на проходе 4 для `0.4.0` / `0.1.0`, намеренно становятся несовместимыми после
этого прохода. Перед Micro-Live нужно построить новый production-equivalent BackTest
на `0.5.0` / `0.2.0` для конкретного symbol/timeframe и текущих параметров.

## Проверка

Локально доступный офлайн-набор после изменений: `246 passed, 2 skipped, 37 subtests`
(PySide6 и Hypothesis отсутствуют в этой Linux-среде). Opt-in soak запущен отдельно и
прошёл: `1 passed`. Авторитетная Windows-проверка выполняется из lock-файла:

```powershell
cd C:\cripta
powershell -ExecutionPolicy Bypass -File .\scripts\setup\_windows.ps1
powershell -ExecutionPolicy Bypass -File .\scripts\check\_windows.ps1
```

Финальная строка успешного прохода:

```text
PASS 5 verification completed successfully.
```
