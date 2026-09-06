# ДИСПЕТЧЕР — АРХИТЕКТУРНЫЙ КОНТРАКТ

**Документ:** `STRATEGY_DISPATCHER_ARCHITECTURE_RU.md`
**Версия:** 2.0
**Дата:** 2026-09-06
**Статус:** канонический специализированный контракт

Верхние контракты:

- `../CRIPTA_ARCHITECTURE_RULES_RU_V1.md`
- `PROJECT_ARCHITECTURE_RU.md`
- `MARKET_CONTEXT_AND_COIN_RATING_ARCHITECTURE_RU.md`

## 1. Назначение

Dispatcher — второй уровень прикладного контура:

```text
MAYAK -> DISPATCHER -> STRATEGY
```

Он получает объективные причинные факты MAYAK и публикует единый прикладной read-model рынка и торговой ёмкости.

Dispatcher не торгует, не знает тип Entry и не принимает решения за Strategy.

## 2. Главная граница

Dispatcher не отвечает на вопрос:

> «Подходит ли этот рынок конкретной Strategy/Entry?»

Он отвечает:

> «Что объективно происходит на рынке и с конкретной монетой прямо сейчас, насколько свежи и качественны эти данные, и какая торговая ёмкость аккаунта доступна?»

Интерпретация принадлежит Strategy.

## 3. Вход Dispatcher

Рыночный вход:

```text
MAYAK SharedMarketContext
+ MAYAK instrument/coin facts
```

Account input:

```text
normalized TradingAccountState
```

`StrategyMarketProfile`, тип Entry, результат сделки, PnL, текущие позиции нашей Strategy и историческая успешность Entry не являются входом объективной рыночной оценки Dispatcher.

## 4. Три класса выходных показателей

### 4.1 GlobalMarketContext

Общерыночная карточка: направление, breadth, synchronization, timeframe state, фактический money flow, liquidity, liquidations, positioning/OI, BTC/ETH/reference state, event context, quality/freshness/provenance.

### 4.2 CoinMarketContext / CoinMarketRating

Для каждого наблюдаемого инструмента Dispatcher публикует объективную динамическую карточку. Минимально допускаются:

```text
symbol
observed_at
market_context_id
coin_context_id / rating_id
rating_version
score / band
spot_money_flow
derivatives_money_flow
money_flow_speed / acceleration
large_trade_activity
open_interest / change / regime
funding / positioning
liquidity_state / resilience
liquidation_state / phase / acceleration
relative_strength
market_synchronization / divergence
event_risk
data_quality / freshness / coverage
provenance
```

Конкретная формула score должна быть causal, versioned и explainable. Отсутствующие данные остаются unknown/unsupported, а не превращаются в ноль или нейтральное состояние.

### 4.3 TradingCapacitySnapshot

Состояние торговой ёмкости аккаунта:

```text
account_state_id
exchange/account identity
observed_at
freshness
total/equity
used
reserved
free
available_for_new_trading
completeness/quality
```

Рыночный rating и account capacity никогда не сливаются в один status.

## 5. Деньги

Dispatcher обязан сохранять физический смысл данных MAYAK.

Фактически исполненный spot flow, фактически исполненный derivatives flow, изменение OI и выставленная ликвидность — разные процессы. Они не заменяют друг друга.

Понятия «деньги входят в монету» / «деньги выходят из монеты» могут публиковаться только как объяснимая агрегация реально наблюдаемых потоков с указанием source, window, magnitude, speed, acceleration и quality.

## 6. Ликвидации

Ликвидации — отдельный объективный датчик принудительного потока и рыночного стресса.

Dispatcher должен уметь показать как минимум направление ликвидаций, денежный объём, интенсивность, breadth, acceleration и phase, не превращая это в торговую команду.

## 7. Рейтинг монеты не является рейтингом Strategy

`CoinMarketRating` описывает саму монету/рынок. Он не должен использовать:

- успешность наших Entry;
- PnL нашей Strategy;
- число текущих LONG/SHORT сигналов;
- наличие нашей позиции;
- результаты backtest/research конкретной Strategy.

`StrategyCoinFit` — отдельный Analyst/research объект. Если Strategy когда-либо использует его, это происходит только через новую owner-approved Strategy version.

## 8. Dispatcher не выбирает направление сделки

Dispatcher может объективно публиковать `money_inflow`, `money_outflow`, bullish/bearish breadth, forced long/short liquidations и другие направленные рыночные факты.

Это не `ENTER_LONG`, `ENTER_SHORT`, `BLOCK_ENTRY` или `CLOSE_POSITION`.

Разные Strategy могут использовать один и тот же контекст противоположно.

## 9. Несколько Strategy

Один `GlobalMarketContext`, `CoinMarketContext` и `TradingCapacitySnapshot` могут быть прочитаны любым количеством Strategy/Entry consumers.

Dispatcher не создаёт отдельную «истину рынка» под каждую Strategy.

## 10. Причинность и версии

Каждый context/rating хранит:

```text
observed_at
created_at
source MAYAK snapshot/context IDs
version/schema/config fingerprint
quality/freshness/coverage
provenance
```

Для решения в T допустим только context с `observed_at <= T`.

## 11. Account capacity не является разрешением на вход

Даже если `available_for_new_trading >= requested_amount`, Dispatcher не говорит «войти». Strategy задаёт policy, Entry принимает конкретное решение.

## 12. Безопасность

Dispatcher не имеет прямого пути к Execution mutations.

Technical fail-closed принадлежит operational safety / Execution для stale/unknown mandatory exchange/account state. Рыночный rating сам по себе техническим safety gate не является.

## 13. Переход от profile-based реализации

Текущие таблицы/сервисы могут исторически содержать `strategy_dispatcher.assessments`, `profile_id`, `GOOD_MATCH`, `INCOMPATIBLE` и другие profile-based оценки.

С версии этого контракта они считаются transitional/legacy research evidence и могут временно продолжать работать только с `trading_effect=NONE` для накопления статистики и сравнения.

Они не определяют будущую каноническую роль Dispatcher и не получают live-влияние автоматически. Их удаление/миграция/замена требует отдельной implementation-задачи.

## 14. Главная формула

> MAYAK наблюдает внешний рынок и формирует причинные факты.

> Dispatcher универсально структурирует эти факты в global/coin context и публикует торговую ёмкость.

> Strategy решает, что объективный контекст означает для её способа торговли.

> Entry принимает решение конкретной попытки.

> Execution исполняет.
