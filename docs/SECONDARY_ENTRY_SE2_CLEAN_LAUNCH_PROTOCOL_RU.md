# SE2 — Secondary Entry Clean Launch Discovery V1

## Статус

Research only. Downloads: **DISABLED / fail-closed**.

SE2 не меняет Entry, основной structural stop `-1.00%`, P50/P51, Exit, Risk,
Execution, live runtime или UI. Это отдельная исследовательская ветка Secondary Entry.

## Исходная гипотеза

SE1 показал, что простой контракт

`достаточный adverse -> rebound X -> Secondary Entry`

слишком часто принимает обычную пилу за подтверждённый поток. При этом post-SE1
анатомия дала физически осмысленную гипотезу: качество Secondary может зависеть от
**чистоты запуска** — малого числа возвратов через Entry-зону и быстрого развития
движения после causal reversal point.

SE2 намеренно является discovery. Он должен найти минимальный causal контракт,
который заслуживает отдельного blind confirmation. Результат ALL9 не является
production rule.

## Источник данных

Только точный machine truth SE1:

`reports\secondary_entry_se1\ALL9_SE1_WORKING\secondary_entry_events.csv`

Ожидаемый SHA256:

`1dca79fdaa452c346d5ff5249d3fb028a8ce33e5788fa6e1e53c89215cf41424`

Ожидаемый SE1 run-contract SHA256:

`2d198b2220adae9cd3f2a997b481e90d4bf85722d8afac009ceecff63a4e82cc`

При несовпадении исследование завершается fail-closed.

NEW5 не читается и не используется ни для отбора, ни для просмотра outcome.

## Causal feature firewall

Фильтр кандидата имеет право использовать только информацию, уже известную к
Secondary Entry:

- SE1 min adverse depth;
- rebound confirmation;
- число zero crossings до Secondary Entry;
- время Main Entry -> reversal point;
- время reversal point -> Secondary Entry;
- полное время Main Entry -> Secondary Entry;
- rebound speed, рассчитанную только на отрезке reversal -> Secondary;
- положение Secondary Entry относительно Main;
- известную structural stop distance.

Future MFE/MAE, target hits и exit reason используются только как outcome labels и
никогда не входят в `Candidate.matches()`.

## Frozen discovery grid

Базовые SE1 параметры:

- adverse: `0.10 / 0.25 / 0.50 / 0.75%`;
- rebound: `0.10 / 0.15 / 0.20 / 0.25 / 0.30%`.

Clean-launch признаки:

- `zero_crossings <= 1 / 2 / 3 / 5 / 8`;
- `touch_to_scale <= 5 / 10 / 15 / 30 / 60 min`;
- `launch_to_scale <= 1 / 2 / 5 / 10 / 15 min`;
- `rebound_speed >= 0.02 / 0.05 / 0.10 / 0.20 pct/min`.

Разрешены только заранее определённые семейства:

- BASE;
- Z;
- T;
- L;
- V;
- Z+T;
- Z+L;
- Z+V.

Kitchen-sink комбинации из трёх и более дополнительных фильтров запрещены.
Всего до просмотра результата формируется **1800** кандидатов.

## Primary benchmark

Primary outcome:

`Secondary +1.10% before SE1 structural stop`.

Illustrative economics:

- margin `$100`;
- leverage `10x`;
- notional `$1000`;
- primary round-trip cost reserve `0.10% notional`;
- winner `+1.10%` -> `$10 net` после этого cost reserve;
- loser -> фактическая structural stop distance от Secondary fill + cost reserve.

Stop остаётся структурным:

`reversal point - 0.10 percentage points`

в directional Main coordinates. Плечо не изменяет stop distance.

Дополнительно сохраняется cost sensitivity для `0.05 / 0.075 / 0.10 / 0.15%`.

## Discovery robustness protocol

Candidate считается pre-bootstrap robustness-pass только если одновременно:

- resolved >= 60;
- ALL9 EV > 0;
- PF >= 1.20;
- EV лучше собственного BASE минимум на `$1.00` на resolved Secondary;
- положительный EV во всех 3 заранее заданных временных 30-дневных folds;
- минимум 7 символов имеют >=5 resolved Secondary;
- минимум 70% evaluable symbols имеют положительный EV;
- крупнейший символ даёт не более 30% всех +1.10 winners.

После этого до пяти non-redundant кандидатов проходят deterministic bootstrap.
Финальный discovery candidate требует `bootstrap EV p05 > 0`.

Это всё равно **не confirmation**.

## Что сохраняется

Machine truth:

- `candidate_grid_results.csv` — все 1800 кандидатов;
- `prebootstrap_candidates.csv`;
- `selected_candidates.csv`;
- `bootstrap_ev.csv`;
- `selected_candidate_scope_detail.csv`;
- `selected_candidate_cost_sensitivity.csv`;
- `selected_candidate_events.csv`;
- `clean_launch_feature_anatomy.csv`;
- `SE2_DISCOVERY_CANDIDATE_MANIFEST.json`;
- `SE2_DISCOVERY_CANDIDATE_MANIFEST.sha256`;
- `provenance.json`;
- `summary.json`;
- `summary.md`.

Для selected candidates отдельно сохраняются:

- ALL9 / DEV2 / HOLDOUT7 diagnostics;
- каждая из 9 монет;
- три temporal folds;
- +0.50 / +1.00 / +1.10 / +2.00 / +3.00;
- EV, PF, net PnL;
- max loss streak;
- cost sensitivity;
- все выбранные event rows с causal features и outcomes.

## Интерпретация

Если `NO_ROBUST_CANDIDATE` — не расширять grid задним числом на тех же ALL9 без
нового заранее сформулированного протокола.

Если `DISCOVERY_CANDIDATES_FOUND` — заморозить emitted manifest/hash и только затем
создавать отдельный confirmation runner. NEW5 нельзя ретюнить: на нём применяется
ровно frozen candidate.
