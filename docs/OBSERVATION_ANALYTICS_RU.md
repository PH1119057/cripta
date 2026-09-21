# CRIPTA — наблюдение, контекст, мониторинг и аналитика

**Версия:** 1.4
**Дата:** 2026-09-21
**Статус:** активный канонический контракт наблюдательно-аналитического контура

Этот документ объединяет MAYAK, Dispatcher, Monitoring, Lifecycle Supervisor,
Position Supervisor, Analyst и Research. Ни один из этих компонентов не получает торговые права из-за
того, что наблюдает, классифицирует или анализирует рынок.

# 1. MAYAK — независимое наблюдение рынка

## 1.1 Назначение

MAYAK отвечает только на вопрос:

> Что объективно происходит во внешнем рынке?

Он работает независимо от наличия активных Strategy, сигналов, позиций и
результатов нашей торговли.

## 1.2 Разрешённые данные

MAYAK может причинно наблюдать и нормализовать:
- public trades и цену;
- spot/derivatives исполненный поток отдельно;
- объём;
- OI;
- funding/premium/mark/index;
- обычный стакан и его динамику;
- ликвидации;
- breadth/synchronization/divergence;
- данные нескольких CEX/DEX;
- on-chain;
- внешние макро/политические события, если источник утверждён;
- data quality/freshness/provenance.

Неизвестные данные фиксируются как неизвестные, а не как ноль.

## 1.3 Запреты MAYAK

MAYAK:
- не знает правила конкретной Strategy;
- не оценивает прибыльность нашей Strategy;
- не создаёт StrategySignal;
- не разрешает и не запрещает Entry;
- не выбирает LONG/SHORT;
- не меняет stop/Exit;
- не отправляет ExecutionRequest;
- не мутирует Exchange;
- не учится автоматически на PnL.

## 1.4 Причинность

Live и historical replay должны использовать одинаковый физический смысл
признаков.

Для состояния в момент T используются только данные, реально доступные к T.

Нет точного raw-источника — слой сохраняет `NO_DATA`, а не синтетическую
замену, если эквивалентность не доказана.

## 1.5 Многобиржевость

Архитектура MAYAK не привязана к Bybit.
Каждый источник подключается адаптером с exact provenance.

## 1.6 Выход MAYAK

Выход — versioned strategy-agnostic market facts/context с:
- event/observed/received times;
- source lineage;
- quality;
- freshness;
- coverage;
- version/fingerprint.

Интерпретация этих фактов за конкретную Strategy происходит downstream.

# 2. DISPATCHER — объективный прикладной контекст

## 2.1 Назначение

Dispatcher находится между MAYAK и Strategy и публикует удобный
strategy-agnostic прикладной контекст.

Он не отвечает на вопрос:
> подходит ли рынок конкретной Strategy?

## 2.2 Основные классы данных

Dispatcher может публиковать:
1. глобальный контекст рынка;
2. контекст конкретного инструмента/монеты;
3. объективный `CoinMarketRating`, если формула отдельно утверждена;
4. состояние торговой ёмкости аккаунта.

Account capacity минимум различает:
- total/equity;
- used;
- reserved;
- free;
- available_for_new_trading;
- freshness;
- source exchange/account.

## 2.3 Границы Dispatcher

Dispatcher:
- не читает PnL Strategy как рыночный признак;
- не знает Entry formula;
- не создаёт Strategy profiles/suitability;
- не включает и не выключает Strategy;
- не выбирает LONG/SHORT;
- не создаёт StrategySignal;
- не резервирует средства по своей воле;
- не отправляет ордер;
- не закрывает позиции.

## 2.4 Strategy-specific интерпретация

Один и тот же Dispatcher context разные Strategy могут трактовать
противоположно.

Историческая пригодность конкретной Strategy для монеты (`StrategyCoinFit`)
относится к Analyst/research, а не к Dispatcher rating.

## 2.5 Качество

Каждый context обязан быть причинным, versioned, quality/freshness-aware и
иметь provenance.

Missing/stale/partial не становятся neutral.

# 3. MONITORING — наблюдение и UI

## 3.1 Два разных смысла мониторинга

Нельзя смешивать:
1. сбор/наблюдение рыночных данных по широкому universe;
2. Strategy-specific мониторинг условий торговли.

## 3.2 Market/coin monitoring

Технический market monitor может наблюдать больше монет, чем торгует конкретная
Strategy.

Наличие symbol в monitor:
- не разрешает торговлю;
- не включает Strategy;
- не создаёт allocation;
- не является Entry condition.

## 3.3 Strategy Monitor

Strategy Monitor отображает состояние как минимум в координате:

```text
Strategy version × symbol × direction
```

Один symbol может иметь несколько строк разных Strategy с разными Entry и
состоянием.

Trading universe конкретной Strategy определяется только её StrategyCard.

## 3.4 UI/read-model

UI/read-model:
- показывает факты;
- позволяет владельцу управлять только разрешёнными control-сущностями;
- не содержит скрытую торговую policy;
- не пересчитывает Entry независимо от канонического runtime;
- неизвестное показывает как неизвестное.

## 3.5 Lifecycle Supervisor

Lifecycle Supervisor — технический сквозной контролёр lifecycle. Он не является
торговым слоем и не владеет trading policy.

Единственная каноническая lifecycle-chain находится в
CRIPTA_ARCHITECTURE_RULES_RU_*.md §9.1. Этот документ её не дублирует.

Supervisor обязан видеть exact lineage/IDs и выявлять нарушение обязательных
handoff/invariants, включая:
- CAPITAL_RESERVATION_STUCK;
- EXCHANGE_POSITION_OWNERSHIP_INVARIANT_BROKEN;
- EXCHANGE_POSITION_MODE_MISMATCH;
- POSITION_WITHOUT_EXIT_OWNER;
- POSITION_WITHOUT_CONFIRMED_INITIAL_PROTECTION;
- потерянный/неподтверждённый required handoff/reconciliation.

EXCHANGE_POSITION_OWNERSHIP_CONFLICT не является critical lifecycle fault:
это штатный fail-closed EntryDecision outcome до real request.

Точные определения tokens принадлежат docs/CRIPTA_GLOSSARY_RU*.md.

Lifecycle Supervisor не имеет права лечить faults торговой догадкой.
Automatic protection reassert/reduce-only close допустим только если exact
Strategy version заранее содержит соответствующую owner-approved emergency/
protection-failure policy.

Critical fault обязан доходить до владельца через durable alert delivery:
событие/очередь, retry, acknowledgement либо explicit escalation state.
Отображение только в UI не считается доставкой. Потеря critical alert delivery
сама фиксируется как operational fault.

Lifecycle Supervisor:
- не создаёт StrategySignal;
- не создаёт EntryDecision/ExitDecision;
- не меняет stop/TP/trailing;
- не закрывает позицию по собственной оценке;
- не выбирает Strategy.

## 3.6 Position Supervisor

Position Supervisor наблюдает конкретную фактически открытую StrategyPosition.

Он может сохранять:
- current price;
- MFE/MAE;
- protection;
- current H9/H3/other Strategy geometry snapshots;
- смещения;
- current MAYAK/Dispatcher context.

Position Supervisor не получает право самостоятельно изменить Exit policy.

Он отвечает на вопрос «что фактически происходит с этой позицией сейчас?»,
тогда как Lifecycle Supervisor отвечает «правильно ли эта сущность проходит
обязательный сквозной lifecycle?».

# 4. ANALYTICS / RESEARCH — доказательный контур

## 4.1 Назначение

Analyst/research объясняет, что произошло, и создаёт доказательный материал.

Он не торгует и не меняет Strategy автоматически.

## 4.2 Единица анализа

```text
StrategyActivation / exact Strategy version
-> EntryPlan + ExitPlan fingerprints
-> causal market refs
-> signal_id (одна Strategy)
-> strategy_attempt_id
-> EntryDecision
-> optional EntryExecutionRequest/order/fill
-> optional StrategyPosition
-> optional ExitDecision
-> optional ExitExecutionRequest/close/protection fill
-> actual economics
```

Две Strategy в один момент имеют два независимых `signal_id`.

## 4.3 Причинность

При анализе состояния в T используются только данные, доступные к T.
Будущие данные используются только как outcome.

Связь по `symbol + ближайшее время` не заменяет exact IDs/source refs, если
точная связь должна существовать.

## 4.4 Observed vs Consumed

Различать:
- `OBSERVED_CONTEXT` — существовавший причинный контекст;
- `CONSUMED_CONTEXT` — контекст, реально использованный EntryPlan/ExitPlan.

## 4.5 Исследование никогда не канон

Любое исследование — старое, новое, OOS, holdout, replay, pilot, full-universe
или завершённое сегодня — остаётся доказательным материалом до отдельного
решения владельца.

Новое исследование не обязано наследовать постановку предыдущего исследования.

Если владелец ставит новый вопрос, исполнитель строит методику от:
1. текущего активного канона;
2. канонического словаря;
3. текущих доступных данных;
4. буквального нового вопроса.

Запрещено рассуждать:
> раньше Entry V1 делал так, поэтому и сейчас начнём с этого

если владелец явно не попросил сравнить с Entry V1.

## 4.6 Переиспользование старых данных

Большой накопленный dataset можно быстро переиспользовать, если доказано, что
его физические поля совместимы с новым вопросом.

Переиспользование данных не означает наследование старых thresholds, старой
Strategy, старого Entry, старых названий или старых выводов.

## 4.7 Новое исследование

До массового расчёта требуется:
- однозначно определить термины;
- зафиксировать объект/границы/единицы;
- проверить причинность;
- выполнить малый сквозной тест;
- проверить физически правдоподобный пример;
- только затем считать полную базу.

Если пользовательская формулировка содержит термин вне словаря — сначала
уточнение, а не догадка.

## 4.8 Геометрические исследования

Запрещён неоднозначный показатель вроде `h3_edge`.

Нужно явно указывать физический объект, например:
- lower_boundary_inner_edge;
- upper_boundary_inner_edge;
- entry_relative_near_edge;
- entry_relative_opposite_edge;
- working_range_width;
- exact depth/timeframe.

## 4.9 Counterfactual / псевдосделки

Если Entry condition выполнился, но real Entry не состоялся по одной из явно
разрешённых admission-причин, Analyst может вести отдельную counterfactual
псевдосделку.

Разрешённые причины минимум:
- INSUFFICIENT_AVAILABLE_FUNDS;
- EXCHANGE_POSITION_OWNERSHIP_CONFLICT.

Counterfactual обязан сохранять exact Strategy/EntryPlan/ExitPlan lineage,
strategy_attempt_id, EntryDecision и counterfactual_block_reason.

Причины не смешиваются: capital shortage и slot conflict остаются разными
аналитическими классами.

Counterfactual:
- не резервирует капитал;
- не получает physical slot claim;
- не создаёт ExecutionRequest;
- не имеет Exchange mutation rights.

STALE_OR_UNKNOWN_REQUIRED_STATE и operational faults автоматически не
превращаются в полноценную псевдосделку, если causal replay нельзя доказать.

Counterfactual outcome всегда отделяется от фактического PnL.

## 4.10 Экономика

Торговые отчёты пользователю формулировать по-русски и различать:
- до комиссий;
- комиссии;
- после комиссий;
- funding;
- slippage;
- фактический PnL;
- counterfactual.

## 4.11 Из исследования в live

```text
ДОКАЗАТЕЛЬСТВА
-> РЕШЕНИЕ ВЛАДЕЛЬЦА
-> НОВАЯ STRATEGY / ВЕРСИЯ ДОКУМЕНТА
-> TEST/SHADOW
-> LIVE EQUIVALENCE
-> MICRO_LIVE
-> LIVE
```

Точные обязательные условия real arm определены в
docs/TRADING_CONTOUR_RU*.md §4.7. Ни liveness сервиса, ни DEPLOY, ни зелёные
unit tests сами по себе не дают LIVE rights.

# 5. Общая граница наблюдательного контура

MAYAK, Dispatcher, Monitoring, Lifecycle Supervisor, Position Supervisor и
Analyst могут расширять видимость системы, но не становятся владельцами торговой policy.

Наблюдение, классификация, рейтинг, корреляция и статистическая полезность сами
по себе не создают право открыть, закрыть или изменить позицию.

## 4.12 Research data contour

Физические источники, основной 20-symbol universe, интервалы raw coverage,
локальные gaps и distinction между historically recoverable и realtime-only
данными определяет `docs/RESEARCH_DATA_CONTOUR_RU*.md`.

Особенно для liquidation:

- exact `allLiquidation` является отдельным market source;
- обычный public trade не доказывает, что он является liquidation;
- отсутствие historical liquidation source = `NO_DATA`;
- money flow, восстановленный из public trades, не означает наличие OI,
  funding или liquidation за тот же момент;
- full-universe research без явного field-level coverage не объявляется
  завершённым.
