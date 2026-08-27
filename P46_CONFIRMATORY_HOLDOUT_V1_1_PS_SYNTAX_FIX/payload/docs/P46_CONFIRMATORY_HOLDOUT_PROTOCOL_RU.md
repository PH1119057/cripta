# P46 — Confirmatory Holdout Entry V1

## Статус

P46 — подтверждающий временной holdout. Он **не является discovery-этапом**.
Новые фильтры, веса, пороги, сочетания признаков и veto по результатам P46 не подбираются.

## Зафиксированные даты

- Discovery, который уже был просмотрен: `2026-05-18 00:00 UTC .. 2026-08-16 00:00 UTC`.
- Freeze протокола выполняется до начала нового holdout.
- Holdout: `2026-08-19 00:00 UTC .. 2026-09-18 00:00 UTC` — ровно 30 суток.
- Последний полный торговый день: `2026-09-17`.
- Для каузального прогрева ATR/зон/market-regime используется история с `2026-08-12`.
  Результаты `2026-08-12 .. 2026-08-19` не входят в confirmatory outcome.

## Замороженные кандидаты

1. `cooldown_60m` — отдельное состояние после структурной неудачи.
2. `p44_residual_q1` — только не-BTC активы; порог q25 берётся дословно из P44 S1.
3. `zone_approach_slope_q1` — только возле aligned S/R <=0.50 ATR; q25 берётся из P45.1 S1.
4. `zone_second_retest` — второй независимый clean-lifecycle retest.
5. `zone_fourth_plus_retest` — четвёртый+ retest как отрицательный контекст.

`60m cooldown` проверяется на всей exact-touch популяции P34.
Остальные кандидаты проверяются на замороженной Core-архитектуре P36:
`accepted_after_failure_embargo && pressure_then_reversal && !oi_tail_danger`.

## Почему P39/P40 не нужны

P39/P40 в discovery добавляли orderbook-признаки, но не определяли сам Core-набор.
P46 реконструирует тот же Core из P36 и поэтому не скачивает десятки гигабайт orderbook-архивов.
Это сокращает runtime и не меняет проверяемую Entry-семантику.

## Primary / secondary outcomes

Primary: `+0.5 before -1.0`, favorable / все сигналы.

Secondary:
- decisive `+0.5/-1.0`;
- `+1.0 before -1.0`, favorable / все;
- decisive `+1.0/-1.0`.

## Предварительно зарегистрированные критерии

Скрипт freeze сохраняет точные sample/transfer-критерии в `FROZEN_PROTOCOL.json` и SHA256-lock.
После freeze файл нельзя менять. Повторный freeze только проверяет идентичность.

Вердикты:

- `SUPPORTED` — выполнены заранее заданные sample + cross-asset direction критерии;
- `NOT_SUPPORTED` — мощности достаточно, но переносимость/направление не подтвердились;
- `UNDERPOWERED` — выборка недостаточна. Это не разрешение менять пороги.

## Порядок работы

Сейчас:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\freeze_p46_confirmatory_holdout_windows.ps1
```

До `2026-09-18 00:00 UTC` outcome-runner намеренно заблокирован.

После окончания holdout:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\prepare_p46_holdout_data_windows.ps1
powershell -ExecutionPolicy Bypass -File .\scripts\research_p46_confirmatory_holdout_windows.ps1
```

## Guardrail

P46 не меняет live trading, Entry enforcement, Exit, Risk, leverage или execution.
Решение о включении признаков в Entry Quality Score принимается только после чтения полного P46 отчёта.
