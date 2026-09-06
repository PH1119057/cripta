# АНАЛИТИК — АРХИТЕКТУРА СЛОЯ ПОСТФАКТУМ-АНАЛИЗА

**Документ:** `ANALYST_ARCHITECTURE_RU.md`
**Версия:** 1.2
**Дата:** 2026-09-06
**Статус:** специализированный архитектурный контракт поддерживающего контура

Верхние контракты:

- `../CRIPTA_ARCHITECTURE_RULES_RU_V1.md`
- `PROJECT_ARCHITECTURE_RU.md`
- `SIGNAL_LIFECYCLE_CONTRACT_RU.md`

## 1. Место Analyst

Analyst не является шестым торговым уровнем.

Он находится в technical/observability support contour вокруг:

```text
MAYAK -> DISPATCHER -> STRATEGY(ENTRY/EXIT) -> EXECUTION -> EXCHANGE
```

## 2. Назначение

Analyst отвечает:

- что происходило на рынке;
- что видел MAYAK;
- что показывал Dispatcher;
- какая Strategy рассматривалась;
- какой Entry decision был принят;
- хватало ли доступного торгового капитала;
- что реально исполнил Execution;
- как Exit сопровождал position;
- что произошло с rejected/no-fill attempt;
- какой слой/компонент был прав или ошибся.

Analyst не торгует.

## 3. Единица анализа

Корневая связь:

```text
signal_id + strategy_attempt_id
```

Для actual trade добавляются `trade_id` и `position_id`.

## 4. Причины отказа

Не объединять все отказы под словом `risk`.

Минимально различать:

```text
STRATEGY_CONDITION_REJECTED
INSUFFICIENT_AVAILABLE_FUNDS
OPERATIONAL_SAFETY_BLOCKED
EXCHANGE_REJECTED
NO_FILL
```

## 5. Деньги

Analyst должен отличить:

> Strategy/market отвергли сделку

от:

> Сделка была допустима по рынку, но капитал был занят или недоступен.

Для этого использовать `TradingCapacitySnapshot` и `strategy_requested_allocation`.

## 6. Dispatcher quality

Analyst отдельно оценивает качество objective Dispatcher global/coin context и account-capacity snapshot: причинность, freshness, coverage, calibration, полезность компонентов и сохранение физического смысла данных MAYAK.

Решение о пригодности среды для конкретной Strategy принадлежит Strategy, поэтому `STRATEGY_CONDITION_REJECTED` нельзя считать «ошибкой Dispatcher» только по факту отказа.

Отказ по недостатку средств не является ошибкой market logic.

## 7. Несколько Strategy

Один signal может дать противоположные attempts.

Analyst сравнивает их отдельно по Strategy/version.

## 8. OBSERVED vs CONSUMED

Context, который существовал, не равен context, который реально использовала Strategy/Entry.

## 9. Временная причинность

Для оценки decision at T использовать только данные `<= T`.

Будущее используется только для outcome.

## 10. Counterfactual

Rejected/no-fill attempts продолжают наблюдаться.

Counterfactual не является actual PnL.

## 11. Position Supervisor

Supervisor публикует наблюдение фактической position.

Analyst может использовать его историю, но Supervisor не является owner Exit.

## 12. Изменения live

Analyst не изменяет MAYAK, Dispatcher, Strategy, Entry, Exit или Execution автоматически.

Путь:

```text
ANALYSIS
-> RESEARCH
-> OWNER DECISION
-> NEW VERSION
-> SHADOW
-> LIVE EQUIVALENCE
-> MICRO_LIVE
-> LIVE
```

## 13. StrategyCoinFit

Analyst может рассчитывать `StrategyCoinFit`: насколько конкретная `strategy_id/version` исторически соответствует конкретной монете по заранее определённым outcome-метрикам.

Это отдельный strategy-specific исследовательский объект. Он не является `CoinMarketRating`, не должен менять MAYAK/Dispatcher и не может автоматически становиться live-фильтром.

Путь к live использованию остаётся: analysis -> research -> owner-approved Strategy version -> shadow -> live equivalence -> micro-live -> live.

## 14. Масштаб

Analyst должен работать при множестве Strategy/bots/positions.

Этот документ не вводит cap и не проектирует allocator.
