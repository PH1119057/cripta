# P52 — MFE + Giveback + Clean Zone Structure V1

## Зачем

P51 показал, что универсальная ступень stop после MFE/recovery слишком хрупкая. P52 проверяет заранее зафиксированную гипотезу: одинаковые по MFE/giveback сделки должны быть разделены по **каузально уже разрешившейся структуре support/resistance**.

## Frozen scope

- 9 старых активов, frozen период Entry V1.
- P50 cohort: 995 сигналов, достигших +0.10 до исходного -1.00.
- Exact outcome: +1.10 first / -1.00 first; 7 data-end остаются censored.
- NEW5 OOS не открываются и не используются.
- Никакие live Entry / Exit / Risk / Execution файлы не меняются.

## Causal contract

Zone event может участвовать только если:

1. его `event_at` не раньше causal +0.10 activation;
2. P45.1 `outcome_at` уже наступил;
3. `outcome_at` не позже фактического baseline outcome (+1.10 или -1.00);
4. ранний разрез использует `outcome_at <= activation + 60m`, а не будущую классификацию события.

Для MFE+giveback state event должен **начаться после возврата к Entry**. Это более строгая версия и исключает событие, которое началось ещё до giveback, хотя разрешилось позднее.

## Четыре frozen structural states

Для LONG защитная зона = support, препятствие = resistance; для SHORT зеркально.

1. `protective_hold_reclaim` — защитная зона удержалась или false-break reclaimed (favorable).
2. `protective_clean_break_against` — защитная зона clean-broken против позиции (adverse).
3. `obstacle_rejection_against` — препятствие отвергло движение (adverse).
4. `obstacle_clean_break_with` — препятствие clean-broken по направлению позиции (favorable).

P45.1 `clean_break` не переопределяется: используется его frozen definition с двумя подтверждающими 15m close.

## Preregistered hypotheses

- H1: `protective_clean_break_against` должен концентрировать будущие full -1; здесь tighter risk может иметь положительный trade-off.
- H2: `obstacle_clean_break_with` должен концентрировать runners; здесь tighter stop должен быть особенно опасен.
- H3: обычный hold/reclaim не считается production gate заранее.
- H4: rejection препятствия adverse, но ожидается слабее protective clean break.

## Fixed stop experiment

Только после MFE (+0.25/+0.50/+0.75/+1.00) → giveback к Entry → первого causal resolved structural event проверяются ровно:

- оставить baseline -1.00 (reference);
- -0.75;
- -0.60;
- -0.50.

Optimizer отсутствует. Для каждого состояния считаются saved full -1, killed +1.10, retention +2/+3 и illustrative economic delta на той же exact baseline economics, что P51.

## Robustness

Каждый эффект выводится отдельно по direction, symbol и month. `N < 20` автоматически помечается `small_sample=true` и остаётся descriptive-only. Положительный aggregate без cross-scope устойчивости не является production evidence.
