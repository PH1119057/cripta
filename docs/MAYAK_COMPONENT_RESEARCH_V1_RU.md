# MAYAK V2 — протокол компонентного исследования по frozen Entry

**Документ:** `MAYAK_COMPONENT_RESEARCH_V1_RU.md`
**Версия:** 1.1
**Дата:** 2026-09-06
**Статус:** frozen research protocol, без live rules

Верхние контракты:

- `MAYAK_OBJECTIVE_CONTEXT_V2_IMPLEMENTATION_RU.md`
- `MAYAK_HISTORICAL_SIGNAL_BACKFILL_RU.md`
- `M3_ENVIRONMENT_RESEARCH_PROTOCOL_RU.md`
- `PROJECT_GOVERNANCE_RU.md`

## 1. Цель

Измерить, несут ли отдельные объективные компоненты MAYAK информацию о последующем исходе frozen Entry, не превращая MAYAK в Strategy-модель.

```text
MAYAK_TRADING_EFFECT = NONE
ENTRY_POLICY_CHANGED = NO
COIN_MARKET_RATING_FITTED = NO
THRESHOLD_SELECTION = NO
```

## 2. Статус выборки

Frozen ALL9 = 1063 сигналов.

Историческое деление:

```text
DEV2 = UNIUSDT + LINKUSDT = 227
HOLDOUT7 = остальные 7 монет = 836
```

`HOLDOUT7` уже просматривался в предыдущих исследованиях и в этом протоколе называется только `HOLDOUT7_DIAGNOSTIC_REUSE`. Он не является новым чистым OOS.

Весь текущий анализ имеет статус `DISCOVERY_SEEN_FROZEN_ALL9`. Любой кандидат требует будущего нового temporal/cross-asset confirmation.

## 3. Outcomes

Исследуются независимо два frozen first-hit исхода:

1. `+0.10% before -1.00%` против `-1.00% before +0.10%`;
2. `+1.10% before -1.00%` против `-1.00% before +1.10%`.

Unresolved/data-end исключаются только из соответствующей бинарной метрики и сохраняются в counts.

## 4. Компоненты первого exact replay

До подключения дополнительных exact sources исследуются только реально доступные в replay признаки:

- derivatives executed flow 1/5/15/30/60m;
- net/turnover/net_share;
- flow speed/acceleration;
- large-trade share;
- turnover ratio to prior window;
- coin return 1/5/15/60m;
- relative-to-panel/BTC/ETH;
- market median return, breadth/synchronization;
- MAYAK confidence/data completeness.

Spot/funding/premium/orderbook/liquidations не получают фиктивных значений. Они добавляются только отдельной feature/source version после доказанного causal backfill.

После отдельного exact OI 5m source backfill + `CausalMayakReplay -> LiveMayakEngine.on_ticker()` разрешается присоединить объективные `positioning_*` поля по точному `signal_key`. Разрешены только:

- `open_interest`;
- OI change 5/15/30/60m;
- OI speed 5m;
- OI acceleration 5m.

OI не получает искусственного direction sign: рост OI сам по себе не является LONG/SHORT фактом. `open_interest` в pooled ALL9 интерпретируется только как scale-dependent diagnostic; переносимость оценивается per-symbol.

После exact funding + causally available mark/index replay разрешается присоединить по `signal_key` только сопоставимые `basis_*` поля:

- `basis_funding_rate`;
- `basis_funding_rate_change_from_previous`;
- `basis_mark_index_premium_pct`.

Абсолютные `basis_mark_price` и `basis_index_price` не являются pooled ALL9 feature из-за несопоставимого price scale между активами. Funding и premium — signed market facts; Analyst может строить их `entry_aligned::` проекцию только в research output.


## 4.1 Frozen extension: exact Spot and liquidity

До просмотра результатов этих слоёв заранее фиксируется следующий набор.

Exact Spot replay через `LiveMayakEngine` добавляет `spot_*` для 1/5/15/30/60m: buy/sell/net/turnover/net_share, speed/acceleration, large-trade share и turnover ratio. Direction-adjusted проекция разрешена только для signed net/speed/acceleration/net_share и существует только в Analyst output.

Exact depth-200 orderbook replay добавляет только нормализуемые pooled признаки: общий imbalance; bid/ask change immediate/1/5/15m; imbalance change 1/5/15m; spread bps; для 5/10/25/50 bps — локальный depth imbalance и доля этого band от полного depth-200 notional. Абсолютный `bid_usd/ask_usd` и абсолютные depth USD не используются как pooled ALL9 признаки из-за несопоставимого масштаба активов.

Для Strategy-specific research разрешена только внешняя проекция: signed imbalance по direction и `entry_support_change` / `entry_opposition_change` (bid/ask для LONG, ask/bid для SHORT). Она не возвращается в MAYAK.

## 5. Direction-adjusted признаки

MAYAK хранит raw objective value. Analyst дополнительно имеет право построить Strategy-specific исследовательскую проекцию:

```text
LONG:  aligned_value = raw_signed_value
SHORT: aligned_value = -raw_signed_value
```

Это относится к signed net flow, speed, acceleration, returns и relative-strength deltas. Такая проекция существует только в research output и не возвращается в MAYAK.

Для market breadth отдельно допускается:

```text
LONG  -> up_share
SHORT -> down_share
```

как `entry_aligned_market_share`.

## 6. Предварительно зафиксированные метрики

Для каждого numeric component и каждого outcome считать без подбора порога:

- valid/missing count;
- good/bad count;
- median good;
- median bad;
- median difference;
- rank-based AUC (`higher value -> good outcome`);
- `abs(AUC - 0.5)` только как размер univariate separation, не как разрешение gate;
- same-sample quartile outcome rates с явной меткой `DISCOVERY_ONLY`.

## 7. Scope robustness

Те же метрики считать для:

```text
ALL9
DEV2
HOLDOUT7_DIAGNOSTIC_REUSE
LONG
SHORT
каждая монета отдельно
```

Смотреть не только pooled uplift, но и знак эффекта между активами. Один сильный актив не является доказательством универсальности.

## 8. Что запрещено

На этом проходе запрещено:

- искать оптимальный cutoff;
- автоматически строить veto/allow;
- выбирать комбинацию признаков по максимуму результата на ALL9;
- называть HOLDOUT7 новым OOS;
- возвращать outcome/Strategy-fit внутрь MAYAK;
- считать signal-level результат portfolio backtest.

## 9. Candidate evidence

Компонент можно вынести в следующий research только если он:

- имеет достаточное causal coverage;
- не держится на нескольких extreme observations;
- показывает согласованный смысл хотя бы на нескольких активах/scope;
- не противоречит физической интерпретации признака;
- сохраняется при последующем новом OOS/confirmation.

На текущем seen sample это остаётся `candidate evidence`, не live rule.

## 10. Следующий уровень

После первого component report:

```text
exact additional source backfill
-> repeat same frozen metrics without changing definitions
-> new temporal/cross-asset OOS
-> owner review
-> only then CoinMarketRating research contract
```
