# SE1 — Secondary Entry Structural Reversal V1

## Статус

Research only. Downloads: DISABLED / fail-closed.

Это отдельная исследовательская ветка `SECONDARY ENTRY`, а не P51/P52 Entry и не Exit/Risk.

## Гипотеза

Первичный Main Entry остаётся неизменным и сохраняет structural stop `-1.00%`. Его роль — дешёвый Probe, который переживает ранний шум.

Если до Main `-1.00%` цена достигает неблагоприятного causal running extreme, а затем подтверждённо отскакивает от него, создаётся НОВЫЙ Secondary Entry по фактической цене момента подтверждения.

Исходная цена Main Entry после этого не является ценовым якорем Secondary Entry.

## Causal launch point

Для long используется текущий минимум directional move, известный на данный момент. Для short применяется симметричная directional coordinate: неблагоприятный extreme также отрицателен.

Будущий локальный минимум/максимум не используется. Если до подтверждения появляется новый более глубокий adverse extreme, launch point обновляется causal.

Secondary trigger исследуется на заранее заданной сетке:

- minimum adverse depth: `0.10 / 0.25 / 0.50 / 0.75%`;
- rebound from current launch point: `0.10 / 0.15 / 0.20 / 0.25 / 0.30%`.

Для каждой пары допускается максимум ОДНА Secondary попытка на один Probe. Повторные re-entry после Secondary stop в SE1 не моделируются.

## Structural stop

Основная гипотеза SE1:

`Secondary structural stop = launch point - 0.10 percentage points`

в directional coordinate относительно Main Entry.

Пример long:

- Main Entry = `0.00%`;
- causal launch/reversal low = `-0.55%`;
- Secondary trigger после rebound;
- Secondary structural stop = `-0.65%` относительно Main Entry.

Но денежный риск Secondary позиции рассчитывается от ЕЁ СОБСТВЕННОГО fill до structural stop. Поэтому расстояние риска переменное и не равно автоматически `0.10%` или `0.20%`.

Leverage не меняет structural stop. При фиксированном `$100 margin x10 ~= $1000 notional` денежный gross risk равен `notional x stop distance`.

## Что собирается

На уровне каждого Entry и каждой grid-комбинации сохраняются:

- causal launch time/price/move;
- фактический theoretical Secondary fill time/price;
- Secondary fill offset относительно Main Entry;
- rebound distance;
- structural stop price и расстояние от Secondary fill;
- gross USD risk при `$100 x10`;
- diagnostic notional/margin для `$2 gross risk`;
- Main `-1` time;
- zero crossings до Secondary Entry;
- time Main Entry -> launch -> Secondary fill;
- Secondary MFE/MAE до structural exit и до 72h horizon;
- first hits Secondary `+0.10/+0.20/+0.25/+0.30/+0.50/+1.00/+1.10/+2/+3/+5%`;
- false-confirmation rows, где Secondary structural stop был выбит;
- диагностические protection variants `activation +0.20/+0.25/+0.30/+0.50 -> floor +0.10`;
- ALL9 / DEV2 / HOLDOUT7 / per-symbol summaries;
- provenance и source fingerprints.

Protection variants — только diagnostics. Они не являются production Exit rule.

## Data / resume

Используются только уже существующие локальные P40 public-trade archives и frozen Entry truth. Network downloads запрещены.

Runner сохраняет `run_contract.json` с SHA256 по config и источникам. Частичный JSONL разрешено resume только при полном совпадении contract. При несовместимом кэше запуск fail-closed.

Внутренний пропуск trade archive внутри имеющегося временного покрытия — hard failure. Концевой data-end маркируется, а не ремонтируется молча.

## Не меняется

- frozen Entry fingerprint/logic;
- Main Entry `-1.00%` structural stop;
- Exit;
- Risk;
- Execution;
- live runtime;
- Bybit orders/credentials;
- P50/P51 и другие Entry research artifacts;
- reports существующих исследований.

## Запуск

Из `C:\cripta`:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\research_secondary_entry_se1_all9_windows.ps1
```

Рабочий каталог (resume):

`C:\cripta\reports\secondary_entry_se1\ALL9_SE1_WORKING`

После успешного завершения runner создаёт ZIP результата в `reports\secondary_entry_se1`.
