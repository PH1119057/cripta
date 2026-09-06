# УНИВЕРСАЛЬНЫЙ РЫНОЧНЫЙ КОНТЕКСТ И РЕЙТИНГ МОНЕТЫ

**Документ:** `MARKET_CONTEXT_AND_COIN_RATING_ARCHITECTURE_RU.md`
**Версия:** 1.0
**Дата:** 2026-09-06
**Статус:** канонический специализированный архитектурный контракт
**Основание:** явное решение владельца 2026-09-06

Верхние контракты:

- `../CRIPTA_ARCHITECTURE_RULES_RU_V1.md`
- `PROJECT_ARCHITECTURE_RU.md`
- `MAYAK_ARCHITECTURE_PRINCIPLES_RU.md`
- `STRATEGY_DISPATCHER_ARCHITECTURE_RU.md`

## 1. Решение владельца

MAYAK и Dispatcher не должны знать тип конкретного Entry и не должны определять пригодность рынка за конкретную Strategy.

Каноническая цепочка смысла:

```text
MAYAK
  объективно наблюдает рынок и инструменты
        ↓
DISPATCHER
  универсально структурирует global/coin context
  + публикует account capacity
        ↓
STRATEGY
  интерпретирует объективный context по своей policy
        ↓
ENTRY / EXIT
  принимают решения в рамках Strategy
```

Один и тот же context может быть полезен пробойной, трендовой, контртрендовой, отскоковой и любой будущей Strategy по-разному.

## 2. MAYAK — источник объективных рыночных фактов

MAYAK не знает наши сигналы, позиции, PnL и success rate.

Он должен причинно наблюдать:

- рынок в целом;
- каждую монету/инструмент;
- spot и derivatives отдельно;
- реально исполненные покупки и продажи;
- OI/positioning;
- ликвидность;
- ликвидации;
- relative strength/synchronization/divergence;
- события, если источник надёжен;
- качество/свежесть/полноту данных.

## 3. Деньги — не цена и не стакан

Для конкретной монеты минимально различать:

```text
SPOT EXECUTED FLOW
buy_usd
sell_usd
net_usd
turnover_usd
flow_speed
flow_acceleration
large_buy_usd
large_sell_usd

DERIVATIVES EXECUTED FLOW
buy_usd
sell_usd
net_usd
turnover_usd
flow_speed
flow_acceleration
large_buy_usd
large_sell_usd

OPEN INTEREST
absolute/value
change
speed
acceleration
regime
```

Стакан остаётся отдельным слоем выставленной ликвидности. `bid/ask wall` нельзя выдавать за факт пришедших денег.

Фраза «деньги входят в монету» допустима только как объяснимая агрегация фактических потоков с указанным окном, величиной, скоростью, ускорением, источником и quality.

## 4. Ликвидации — обязательный независимый слой

Ликвидации показывают принудительно снятый риск/капитал и стресс рынка, а не добровольный вход новых денег.

Хранить отдельно:

```text
long liquidations
short liquidations
usd amount
event count
affected symbols / breadth
intensity
speed
acceleration
phase
normalization vs own history
```

Главный смысл анализа возникает в сочетании:

```text
LIQUIDATIONS
+ EXECUTED MONEY FLOW
+ OI
+ LIQUIDITY
+ PRICE RESPONSE
```

Например, каскад ликвидаций с продолжающимся оттоком spot денег — другой рынок, чем затухающий каскад с новым spot-покупателем и восстановлением ликвидности. MAYAK/Dispatcher описывают различие, Strategy решает, что с ним делать.

## 5. Объективная карточка монеты

Для каждого инструмента должен существовать причинный `CoinMarketContext`.

Он должен позволять человеку и Strategy ответить:

- деньги реально входят, выходят или поток смешанный;
- spot подтверждает derivatives или расходится с ними;
- OI растёт/падает и как это связано с ценой;
- крупные участники активны или нет;
- ликвидность появляется, снимается или восстанавливается;
- идут ли ликвидации и на какой фазе;
- монета сильнее/слабее общего рынка;
- поведение синхронно рынку или idiosyncratic;
- есть ли известный событийный риск;
- достаточно ли качественны данные.

## 6. CoinMarketRating

`CoinMarketRating` — strategy-agnostic динамический рейтинг объективного состояния монеты.

Требования:

- causal: только данные `<= observed_at`;
- versioned: formula/schema/config fingerprint;
- explainable: score раскладывается на компоненты;
- quality-aware: unknown не превращается в zero;
- dynamic: не существует вечного ярлыка «хорошая/плохая монета»;
- strategy-independent: не читает наши Entry/PnL;
- direction-neutral as a decision: может описывать inflow/outflow и bullish/bearish факты, но не выдаёт `ENTER_LONG/SHORT`.

Рекомендуемый read-model:

```text
symbol
observed_at
coin_market_rating
rating_band

money_flow_score
liquidation_score
liquidity_score
positioning_score
relative_strength_score
event_risk_score
data_quality_score

explanation_ru
source_context_ids
version/fingerprint
```

Числа и формула score этим архитектурным документом не утверждаются. Они требуют отдельного research/implementation contract.

## 7. StrategyCoinFit — другое понятие

`StrategyCoinFit` отвечает на другой вопрос:

> Насколько конкретная `strategy_id/version` исторически работает на этой монете?

Примеры метрик:

- first-hit success;
- stop frequency;
- MFE/MAE;
- speed to validation/target;
- recovery/runner behavior;
- стабильность по периодам и режимам.

Владелец: Analyst/research.

`StrategyCoinFit` не является фактом внешнего рынка и запрещён как вход MAYAK или objective `CoinMarketRating`.

Если Strategy использует его при выборе инструмента, это новая owner-approved Strategy policy/version.

## 8. Несколько одновременно доступных монет

Архитектура допускает, что Strategy/Entry при нескольких потенциальных attempts читает:

```text
objective CoinMarketRating
+ objective current money/liquidation/liquidity context
+ StrategyCoinFit (если утверждён Strategy policy)
+ geometry/Entry conditions конкретной Strategy
+ account capacity
```

Это позволяет в будущем не обязательно брать первую коснувшуюся монету, если Strategy policy предпочитает более качественный доступный инструмент.

Однако конкретный механизм приоритета, ожидания, reservation, capital competition и выбора между одновременными attempts является отдельным будущим Strategy/portfolio contract. Этот документ его не придумывает.

## 9. Idiosyncratic/event context

Монета может временно жить отдельно от общего рынка из-за:

- token unlock/emission;
- exploit/hack;
- listing/delisting;
- governance/DAO event;
- legal/regulatory event;
- protocol incident;
- issuer/foundation action;
- крупного технического/инфраструктурного события.

MAYAK должен уметь обнаруживать статистическое расхождение даже без известной причины. Если надёжный event source доступен, причина добавляется отдельно с provenance. Отсутствие event data не означает `NO_EVENT`; оно означает unknown/no-source.

## 10. Наблюдательный режим

До отдельного owner decision:

```text
MAYAK_TRADING_EFFECT = NONE
DISPATCHER_TRADING_EFFECT = NONE
```

Накопление данных продолжается через causal observed/shadow context.

Никакой новый CoinMarketRating, money-flow interpretation или liquidation feature не становится live gate автоматически.

Путь:

```text
OBSERVATION
-> ANALYSIS
-> RESEARCH
-> OWNER-APPROVED STRATEGY VERSION (если требуется влияние)
-> SHADOW
-> LIVE EQUIVALENCE
-> MICRO_LIVE
-> LIVE
```

## 11. Переходный текущий Dispatcher

Существующий profile-based Dispatcher (`GOOD_MATCH`, `PARTIAL_MATCH`, `POOR_MATCH`, `INCOMPATIBLE`, `profile_id/version`) возник до этого owner decision.

Он может оставаться включённым как shadow/research evidence с `trading_effect=NONE`, чтобы не терять накопление истории.

Он не является каноническим будущим смыслом Dispatcher и не должен получать trading rights. Отдельная implementation-задача позже должна заменить/разделить его на universal global/coin context, сохранив исторический audit.

## 12. Неприкосновенные границы

Запрещено:

- обучать MAYAK на PnL Strategy;
- делать CoinMarketRating из success rate наших Entry;
- давать Dispatcher знание логики конкретного Entry;
- превращать inflow/outflow в приказ LONG/SHORT;
- превращать rating в universal BLOCK/CLOSE;
- смешивать account capacity с рыночным quality score;
- автоматически менять Strategy по статистике Analyst.

## 13. Итог

> MAYAK видит рынок и реальные физические процессы.

> Dispatcher универсально собирает это в понятный global/coin context и rating.

> Analyst отдельно измеряет, насколько конкретная Strategy дружит с конкретной монетой.

> Strategy решает, что всё это означает для её торговли.

> Entry/Exit действуют только в рамках Strategy.
