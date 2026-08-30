# BYBIT — ПУБЛИЧНЫЕ ДАННЫЕ ДЛЯ МАЯКА И ДИСПЕТЧЕРА

**Документ:** `BYBIT_PUBLIC_DATA_FOR_MAYAK_DISPATCHER_RU.md`  
**Версия:** 1.0  
**Дата:** 2026-08-30  
**Статус:** архитектурно-аналитический справочник

## 1. Назначение

Документ фиксирует публичные источники Bybit, которые можно использовать как объективные внешние данные Маяка и затем передавать Диспетчеру через `dispatcher_handoff`.

Правильная цепочка:

```text
BYBIT PUBLIC DATA
       ↓
     MAYAK
       ↓
объективные характеристики рынка
       ↓
dispatcher_handoff
       ↓
STRATEGY DISPATCHER
       ↓
оценка пригодности среды
```

Ни один отдельный показатель Bybit не является готовой торговой командой.

## 2. Региональные endpoints

Для Bybit Kazakhstan учитывать официальный региональный host:

`stream.bybit.kz`

Публичные классы WebSocket:

- `/v5/public/spot`
- `/v5/public/linear`
- `/v5/public/inverse`
- `/v5/public/option`
- `/v5/public/misc/status`

Источник: https://bybit-exchange.github.io/docs/v5/ws/connect


## 3. Приоритеты

### A+ — обязательные
1. Все ликвидации.
2. Реальные сделки Spot и Derivatives.
3. Open Interest.
4. Mark / Index / Last / Premium.
5. Funding.
6. Orderbook.
7. BTC / ETH как отдельные общерыночные контексты.

### A — очень высокий приоритет
8. Long/Short account ratio.
9. RPI orderbook.
10. RPI executions и block trades.
11. Нормализация всех показателей относительно собственной нормы инструмента.

### B — сильное дальнейшее обогащение
12. BTC/ETH Options.
13. Historical volatility.
14. Futures basis / term structure.
15. Insurance pool.

### C — позднее
16. Несколько CEX.
17. DEX.
18. On-chain exchange flows.
19. Внешние макроэкономические/политические источники.


## 4. Public Trades

Topic: `publicTrade.{symbol}`

Доступно для Spot, Futures и Options.

Основные поля:

- `T` — время исполнения;
- `s` — symbol;
- `S` — taker side Buy/Sell;
- `v` — размер;
- `p` — цена.

Поток также может содержать признаки `BT` (block trade) и `RPI` (RPI execution).

Источник: https://bybit-exchange.github.io/docs/v5/websocket/public/trade

### Что считать

Отдельно для Spot и Derivatives:

- buy/sell notional за 1/5/15/30 минут;
- net flow;
- сила потока;
- ускорение;
- turnover;
- turnover относительно нормы;
- доля крупных сделок;
- доля block trades;
- доля RPI executions.

Все показатели хранить и абсолютно, и относительно собственной причинной нормы инструмента.

Spot и Derivatives не смешивать. Отдельно строить `SPOT_DERIVATIVES_ALIGNMENT`.


## 5. Ticker — центральный срочный источник

Topic: `tickers.{symbol}`.

Для Linear/Inverse доступны, среди прочего:

- `lastPrice`;
- `markPrice`;
- `indexPrice`;
- `openInterest`;
- `openInterestValue`;
- `fundingRate`;
- `nextFundingTime`;
- best bid/ask;
- turnover/volume.

Derivatives ticker публикуется примерно каждые 100 ms. Delta может не повторять неизменившиеся поля.

Источник: https://bybit-exchange.github.io/docs/v5/websocket/public/ticker

### Open Interest

Хранить:

- текущее OI и денежную стоимость;
- изменения 5/15/30/60m;
- проценты;
- скорость;
- ускорение;
- значение относительно собственной нормы.

История должна быть time-based, а не ограниченной числом websocket updates.

Канонические состояния:

- `PRICE_UP_OI_UP`
- `PRICE_UP_OI_DOWN`
- `PRICE_DOWN_OI_UP`
- `PRICE_DOWN_OI_DOWN`
- `MIXED`


## 6. Mark / Index / Last / Premium

Это важный слой внутреннего состояния derivatives.

Считать:

- last vs index;
- mark vs index;
- last vs mark;
- скорость и ускорение расхождения.

Bybit также предоставляет исторические:

- Mark Price Kline  
  https://bybit-exchange.github.io/docs/v5/market/mark-kline
- Index Price Kline  
  https://bybit-exchange.github.io/docs/v5/market/index-kline
- Premium Index Kline  
  https://bybit-exchange.github.io/docs/v5/market/premium-index-kline

Предлагаемый универсальный признак Маяка:

`DERIVATIVES_PREMIUM_STRESS`

Входы могут включать premium, OI, funding, derivatives flow и liquidations. Порогов для live не задавать до статистического исследования.


## 7. Funding

Хранить не только абсолютный funding rate:

- funding rate;
- изменение;
- ускорение;
- отклонение от собственной истории;
- время до следующего расчёта;
- экстремальность.

Исследовать сочетания:

- funding + OI;
- funding + price;
- funding + premium;
- funding + liquidations.

Потенциальный универсальный признак: `FUNDING_STRESS`.

Funding вместе с OI и positioning может участвовать в `POSITIONING_CROWDING`, но Маяк не делает торговую команду.


## 8. Long/Short Account Ratio

REST: `/v5/market/account-ratio`

Периоды:

- 5min;
- 15min;
- 30min;
- 1h;
- 4h;
- 1d.

Источник: https://bybit-exchange.github.io/docs/v5/market/long-short-ratio

Критическое ограничение: это отношение **количества аккаунтов**, а не денег. Нельзя интерпретировать `70% LONG holders` как `70% капитала LONG`.

Использовать только вместе с OI, funding, premium, executed flow и liquidations.


## 9. All Liquidation — обязательный слой

Public WebSocket:

`allLiquidation.{symbol}`

Push frequency: 500 ms.

Покрытие: USDT, USDC и inverse contracts.

Источник: https://bybit-exchange.github.io/docs/v5/websocket/public/all-liquidation

Поля:

- `T` — время;
- `s` — symbol;
- `S` — сторона позиции;
- `v` — размер;
- `p` — bankruptcy price.

Важно закрепить тестом:

- `S=Buy` означает ликвидирован LONG;
- `S=Sell` означает ликвидирован SHORT.

### Метрики

Для каждого symbol:

- LONG/SHORT count за 1/5/15/30m;
- LONG/SHORT notional за 1/5/15/30m;
- скорость;
- ускорение;
- нормализация относительно собственной истории.

Market-wide:

- число символов с LONG liquidations;
- число символов с SHORT liquidations;
- breadth каскада.

### Фазы

- `TENSION_BUILDING`
- `CASCADE`
- `EXHAUSTION`
- `RECOVERY`

Фазы наблюдательные, не торговые команды.


## 10. Логика фаз ликвидаций

### TENSION_BUILDING
Возможные признаки:

- crowding высокий;
- OI высокий/растёт;
- funding экстремальный;
- premium ухудшается;
- защитная ликвидность уходит;
- первые ликвидации ускоряются.

### CASCADE
Пример:

- price ↓;
- sell flow ↑;
- LONG liquidations ↑↑;
- liquidation breadth ↑;
- bid liquidity withdrawing;
- synchronization ↑;
- BTC/ETH подтверждают.

### EXHAUSTION
Пример:

- цена ещё падает;
- ликвидации уже не ускоряются;
- OI резко сокращается;
- sell pressure перестаёт ускоряться;
- impact продаж падает;
- absorption растёт;
- bid liquidity возвращается.

### RECOVERY
- каскад заметно ослабевает;
- новые минимумы перестают формироваться;
- покупательская ликвидность возвращается;
- executed selling нормализуется;
- breadth перестаёт ухудшаться.


## 11. Orderbook

WebSocket: `orderbook.{depth}.{symbol}`

Для Spot/Linear доступны глубины 1/50/200/1000.

Источник: https://bybit-exchange.github.io/docs/v5/websocket/public/orderbook

Обычный orderbook **не содержит RPI orders**.

### Метрики стакана

- bid/ask depth ±5 bps;
- ±10 bps;
- ±25 bps;
- ±50 bps;
- book slope;
- microprice;
- liquidity asymmetry;
- bid/ask withdrawal rate;
- bid/ask replenishment rate;
- replenishment latency;
- post-trade resilience.

Очень важен не только imbalance, а реакция стакана после сильного исполнения.

`cts` orderbook можно причинно сопоставлять с `T` public trade.


## 12. RPI Orderbook и специальные исполнения

Bybit предоставляет отдельный RPI orderbook.

Источник: https://bybit-exchange.github.io/docs/v5/market/rpi-orderbook

Покрытие: Spot, USDT/USDC contracts, inverse. Depth 50.

Это позволяет отдельно наблюдать:

- обычную отображаемую ликвидность;
- RPI liquidity;
- RPI executions;
- block trades.

Public Trade может маркировать `RPI` и `BT`.

Полезные метрики:

- RPI liquidity share;
- RPI execution notional;
- block trade notional;
- block/RPI share;
- absorption с учётом RPI;
- liquidity resilience.

Крупные сделки нормализовать относительно собственной истории инструмента, а не фиксированного долларового порога.


## 13. BTC и ETH — полный отдельный контекст

Для BTC и ETH Маяк должен по возможности иметь полный набор:

- price;
- Spot flow;
- Derivatives flow;
- OI;
- funding;
- premium;
- orderbook;
- RPI;
- liquidations;
- options risk.

Не сводить BTC/ETH к простому `UP/DOWN`.


## 14. Options — общерыночный датчик ожиданий риска

Публичный option WebSocket: `/v5/public/option`.

Option ticker содержит:

- bid/ask/mark IV;
- underlying price;
- OI;
- turnover;
- volume;
- delta;
- gamma;
- vega;
- theta.

Источник: https://bybit-exchange.github.io/docs/v5/websocket/public/ticker

Для BTC/ETH можно строить:

- ATM IV;
- short/medium/long-term IV;
- IV term structure;
- IV change/acceleration;
- put-call IV skew;
- option OI/turnover/volume.

Универсальный признак: `OPTION_RISK`.

Options могут давать ранний признак роста ожидаемой волатильности, особенно перед крупными событиями.

Ограничение: публичный OI не даёт права утверждать фактический dealer gamma exposure. Нельзя выдавать такие выводы как доказанный факт без отдельного источника.


## 15. Historical Volatility

REST: `/v5/market/historical-volatility`

Источник: https://bybit-exchange.github.io/docs/v5/market/iv

Данные почасовые. Доступны периоды вроде 7/14/21/30/60/90/180/270 дней в зависимости от актива.

Полезный слой:

`IV_PREMIUM_TO_HISTORICAL_VOLATILITY`

Если option IV растёт значительно быстрее historical volatility, рынок может заранее платить повышенную цену за будущую неопределённость.


## 16. Futures Basis / Curve

Для dated futures ticker может содержать basis/basis rate/year и delivery time.

Полезно строить для BTC/ETH:

- perpetual;
- near future;
- far future;
- contango;
- flattening;
- backwardation;
- basis change;
- curve inversion.

Это медленный sentiment/risk layer, а не микро-Entry сигнал.


## 17. Insurance Pool

REST: `/v5/market/insurance`

Источник: https://bybit-exchange.github.io/docs/v5/market/insurance

Bybit указывает:

- isolated insurance pool обновляется примерно раз в минуту;
- shared pool — примерно раз в сутки;
- в экстремальной волатильности возможна задержка.

Хранить:

- balance;
- USD value;
- change;
- drawdown.

Использовать как медленный `SYSTEMIC_STRESS_CONTEXT`.


## 18. Exchange System Status и качество наблюдения

Публичный WebSocket System Status:

`/v5/public/misc/status`

Источник: https://bybit-exchange.github.io/docs/v5/ws/connect

Маяк должен хранить отдельно:

- Bybit system status;
- Spot WS;
- Linear WS;
- Option WS;
- последнее транспортное сообщение;
- reconnect count;
- exchange event time;
- local receive time;
- server clock drift where available.

Это позволяет отличить `рынок тихий` от `источник умер`.


## 19. Что Bybit публично НЕ даёт напрямую

Без отдельного источника нельзя считать доступными:

- агрегированные депозиты всех клиентов;
- агрегированные withdrawals всех клиентов;
- реальный exchange-wide customer net inflow;
- точную карту будущих ликвидаций всех аккаунтов;
- фактическое плечо каждого участника;
- dealer positioning;
- полную идентификацию внешних кошельков.

Нельзя подменять эти данные OI, funding, orderbook или liquidations.

On-chain/exchange-flow слой должен быть отдельным адаптером; при отсутствии — `NO_DATA`.


## 20. Универсальные характеристики, которые можно построить уже на Bybit

- `DIRECTION`
- `DIRECTION_STRENGTH`
- `VOLATILITY`
- `BREADTH`
- `SYNCHRONIZATION`
- `SPOT_PRESSURE`
- `DERIVATIVES_PRESSURE`
- `SPOT_DERIVATIVES_ALIGNMENT`
- `OI_REGIME`
- `PRICE_OI_STATE`
- `FUNDING_STRESS`
- `POSITIONING_CROWDING`
- `DERIVATIVES_PREMIUM_STRESS`
- `LIQUIDITY_QUALITY`
- `LIQUIDITY_TREND`
- `LIQUIDITY_RESILIENCE`
- `ABSORPTION`
- `LIQUIDATION_INTENSITY`
- `LIQUIDATION_ACCELERATION`
- `LIQUIDATION_BREADTH`
- `LIQUIDATION_PHASE`
- `BTC_STATE`
- `ETH_STATE`
- `OPTION_RISK`
- `FUTURES_CURVE_STATE`
- `SYSTEMIC_STRESS`
- `DATA_QUALITY`


## 21. Что передавать Диспетчеру

Диспетчеру не нужен сырой Bybit payload.

Через `dispatcher_handoff` передавать каноническую характеристику:

```json
{
  "feature": "liquidation.phase",
  "value": "CASCADE",
  "status": "LIVE",
  "confidence": 0.91,
  "observed_at": "...",
  "coverage": "18/20",
  "source": "bybit.linear.allLiquidation"
}
```

Для каждого признака confidence должен быть собственным и учитывать:

- coverage;
- freshness;
- transport;
- warmup;
- availability;
- согласие источников, где применимо.

Для отсутствующих данных:

```text
status = NO_DATA
observed_at = null
```

Не подставлять `0`, `NEUTRAL` или ложное время снимка.


## 22. Практический порядок внедрения

### BYBIT-P1
Ликвидации: `allLiquidation`.

### BYBIT-P2
Derivative stress: mark/index/last/premium + funding + OI.

### BYBIT-P3
Liquidity: обычный стакан + RPI + RPI executions + block trades + resilience/absorption.

### BYBIT-P4
Crowding: Long/Short account ratio + funding + OI + premium.

### BYBIT-P5
Options: BTC/ETH IV, skew, term structure, OI.

### BYBIT-P6
Slow context: futures curve + insurance pool.


## 23. Приоритет для M3 V1

Для текущей консервативной стратегии особенно интересны:

- `LIQUIDATION_PHASE`
- `LIQUIDATION_ACCELERATION`
- `DERIVATIVES_PREMIUM_STRESS`
- `OI_REGIME`
- `FUNDING_STRESS`
- `LIQUIDITY_TREND`
- `LIQUIDITY_RESILIENCE`
- `ABSORPTION`
- `BREADTH`
- `SYNCHRONIZATION`
- `BTC_STATE`
- `ETH_STATE`

Но M3 не должна читать сырые API-поля напрямую.

Правильно:

```text
Bybit
 ↓
Mayak
 ↓
Dispatcher
 ↓
M3 ENTRY/HOLD profile
```

Для будущего M3 HOLD особенно важно исследовать различие:

```text
синхронное ухудшение
+ sell flow ускоряется
+ LONG liquidations ускоряются
+ bid liquidity уходит
+ OI показывает stress/deleveraging
+ BTC/ETH подтверждают
```

против:

```text
цена ещё падает
+ ликвидации уже замедляются
+ OI резко сокращается
+ sell pressure больше не ускоряется
+ absorption растёт
+ bid liquidity возвращается
```

Первая комбинация может описывать развивающийся общий риск, вторая — потенциальное истощение. Это исследовательская гипотеза, а не готовый Exit rule.


## 24. Статистический контракт

Для каждого нового Bybit-признака сохранять:

- raw source timestamp;
- receive timestamp;
- derived feature;
- feature version;
- normalization baseline version;
- status;
- confidence;
- coverage;
- source/venue/market type.

Позднее причинно связывать с signal/entry/fill/position/exit через отдельный correlator.

Trade outcome не должен влиять обратно на Mayak.

Главный критерий полезности:

> показатель должен устойчиво различать состояния рынка на unseen данных и после отдельного research давать положительную net value конкретному потребителю.


## 25. Официальные источники

- Connect: https://bybit-exchange.github.io/docs/v5/ws/connect
- Public Trade: https://bybit-exchange.github.io/docs/v5/websocket/public/trade
- Ticker: https://bybit-exchange.github.io/docs/v5/websocket/public/ticker
- Orderbook: https://bybit-exchange.github.io/docs/v5/websocket/public/orderbook
- All Liquidation: https://bybit-exchange.github.io/docs/v5/websocket/public/all-liquidation
- Long/Short Ratio: https://bybit-exchange.github.io/docs/v5/market/long-short-ratio
- RPI Orderbook: https://bybit-exchange.github.io/docs/v5/market/rpi-orderbook
- Mark Price Kline: https://bybit-exchange.github.io/docs/v5/market/mark-kline
- Index Price Kline: https://bybit-exchange.github.io/docs/v5/market/index-kline
- Premium Index Kline: https://bybit-exchange.github.io/docs/v5/market/premium-index-kline
- Historical Volatility: https://bybit-exchange.github.io/docs/v5/market/iv
- Insurance Pool: https://bybit-exchange.github.io/docs/v5/market/insurance

## 26. Итог

Bybit уже публично даёт значительно более богатую картину рынка, чем обычно используется в простом торговом боте.

Наиболее ценные ещё не полностью использованные источники:

1. All Liquidation.
2. Mark / Index / Premium / Funding / OI.
3. RPI orderbook + RPI/Block executions.
4. Long/Short Ratio.
5. BTC/ETH Options.
6. Futures Curve.
7. Insurance Pool.
8. System Status + transport metrics.

Главный принцип:

> сначала собрать объективные данные, затем построить причинные универсальные характеристики Маяка, затем передать их Диспетчеру и только после этого исследовать применение конкретными торговыми стратегиями.
