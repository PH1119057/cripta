# EO3 — Full Path / MFE–Giveback–Retest Anatomy V1

## Назначение

EO3 — research-only проход по **всем 846** фактически заполненным EO1 `ADVERSE_0P20` Entry. Выборка не делится заранее на winners/losers и не оптимизируется по будущему результату.

## Торговая граница

- фактический Entry: тот же EO1 `-0.20%` fill;
- hard stop: неизменный `-1.00%` от фактического fill;
- `+0.10 floor`, BE, trailing: **DISABLED**;
- profit target: **DISABLED FOR ANATOMY**;
- `+1.10%` и другие положительные уровни — только milestones, не exits;
- торговая жизнь заканчивается только по `-1.00%` hard stop или в конце frozen data `2026-08-16T00:00:00Z`.

## Что измеряется по каждой сделке

1. Полный MFE/MAE до hard stop/data end.
2. Первое достижение `+0.10/+0.20/+0.30/+0.50/+0.75/+1.00/+1.10/+1.50/+2/+3/+5/+10/+20%`.
3. Возврат к Entry после достигнутых milestones.
4. Giveback от текущего running MFE на causal 1m close.
5. После activation `+0.30/+0.50/+0.75/+1/+1.10/+1.50/+2/+3/+5`: максимальный giveback, новый high после giveback, возврат к Entry, последующий hard stop.
6. Восстановление после adverse excursion `-0.10/-0.20/-0.30/-0.50/-0.75/-1.00%`.
7. MFE/MAE и alive-state на горизонтах 1/3/6/12/24/48/72h.
8. Для hard-stop случаев — research-only 72h post-stop continuation: вернулась ли цена к Entry, `+0.50`, `+1.10`.
9. Сколько новых EO1 signals и новых `-0.20` fills по тому же symbol возникло, пока старая позиция оставалась живой.

## Временное разрешение

Основная анатомия строится на causal 1m OHLC, агрегированном только из локального public-trade tape. Fill-minute и hard-stop-minute разрешаются по raw trade ticks, чтобы не включить движение до фактического fill или после фактического stop.

Giveback фиксируется на **1m close**, поэтому EO3 не придумывает порядок High/Low внутри минуты. Это сознательный causal контракт для будущего Exit research.

## Cache / resume

EO3 строит per-day 1m cache в `var\eo3_full_path_1m_cache`. Cache валидируется по версии, symbol, имени raw archive, размеру и `mtime_ns`. Несовместимый cache не используется и строится заново. Downloads запрещены.

## Machine truth

- `eo3_trade_anatomy.csv`
- `eo3_milestone_events.csv`
- `eo3_activation_anatomy.csv`
- `eo3_giveback_events.csv`
- `eo3_adverse_recovery.csv`
- `eo3_overlap_events.csv`
- `per_symbol.csv`
- `summary.json`
- `provenance.json`
- `SUMMARY_RU.md`

## Anti-overfit

EO3 **не выбирает Exit rule** и не ранжирует thresholds по прибыли. Его задача — описать полный слой поведения всех 846 fills. Любая Exit-гипотеза формулируется только после EO3 и должна проверяться отдельно, с простой benchmark-моделью и без повторного использования clean holdout как нового discovery.

## Не меняет

Frozen Entry fingerprint, EO1/EO2, P50/P51/P53, SE1/SE2, Exit, Risk, Execution, MAYAK, live runtime и UI не изменяются.
