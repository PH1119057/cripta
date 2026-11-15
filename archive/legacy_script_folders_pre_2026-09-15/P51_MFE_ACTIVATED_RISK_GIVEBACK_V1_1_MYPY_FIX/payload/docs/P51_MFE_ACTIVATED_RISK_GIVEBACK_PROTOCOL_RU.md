# P51 — MFE-Activated Risk / Giveback Anatomy

**Статус:** discovery research only  
**Downloads:** DISABLED / fail-closed  
**Entry V1 / frozen P46 / live Execution / Exit / Risk:** не меняются.

## Зачем P51

P49/P50 показали две вещи одновременно: исходный structural `-1.00%` нужен ранней сделке, но простая лестница «после N возвратов затянуть stop» режет слишком много будущих runners. В exact untouched baseline `+1.10/-1.00` внутри 995 первоначально правильных Entry есть 394 сделки, которые сначала достигли `+0.10%`, но затем получили `-1.00%` раньше `+1.10%`.

P51 проверяет более узкую causal-гипотезу: **сколько рынок уже успел дать (MFE) до возврата к Entry и последующего подтверждённого восстановления `+0.10%`**.

## Фиксированная матрица

Никакого optimizer/grid search нет.

- MFE milestones: `+0.25 / +0.50 / +0.75 / +1.00%`.
- Candidate tightened stops: `-0.75 / -0.60 / -0.50%`.
- Future targets: `+1.10 / +2.00 / +3.00%`.
- Horizon: 72h.
- Cohort: ровно 995 Entry `+0.10 before -1.00` из P50.

## Causal action

Основной rule candidate для каждого MFE milestone:

1. milestone уже был достигнут;
2. цена после этого вернулась к Entry (`<= 0%` относительно фактического Entry);
3. этот Entry-zone visit завершился causal recovery обратно к `+0.10%`;
4. **только в этот момент** разрешается моделировать tightening stop.

Для milestone используется **первый** такой recovery. Это не hindsight low: действие происходит после фактически наблюдаемого recovery.

Отдельно строится descriptive matrix по recovery #1..#6, но номер возврата не считается production trigger сам по себе.

## Exact +1.10 baseline

P51 обязательно читает уже рассчитанный untouched baseline `-1.00 vs +1.10` и fail-closed проверяет:

- ALL9 = 1063 сигналов;
- P50 cohort = 995;
- внутри cohort: 594 `+1.10 first`, 394 `-1.00 first`, 7 data end;
- raw 72h path каждого сигнала даёт тот же first-touch outcome и event time (допуск 1s).

Если equivalence не выполняется, P51 останавливается без формирования вывода.

## Что измеряется

- saved full-stop losers;
- killed future `+1.10/+2/+3` runners;
- required adverse room будущих runners после causal action;
- giveback после каждого MFE milestone до первого следующего Entry-zone visit;
- MFE, который успели показать 394 будущих `-1` losers;
- LONG/SHORT и asset stability;
- illustrative economic delta для exact `+1.10/-1.00` baseline с тем же модельным cost contract, что в исходном baseline.

Экономика descriptive: она не моделирует slippage, funding, портфельную конкуренцию и реальные fee tiers.

## OOS discipline

BNB/AVAX/SUI/AAVE/LTC не читаются. P51 не выбирает production stop. После P51 допускается сформулировать максимум 1–2 простых кандидата; только затем отдельным решением открыть зарезервированный OOS.
