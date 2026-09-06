# MAYAK V2 — CROSS-ASSET OOS CONFIRMATION FROZEN COMPONENTS V1

**Документ:** `MAYAK_COMPONENT_OOS_CONFIRMATION_V1_RU.md`
**Версия:** 1.0
**Дата:** 2026-09-06
**Статус:** LEVEL 5 · pre-registered research protocol; results not opened

Верхние контракты:

- `../CRIPTA_ASSISTANT_WORK_RULES_RU_V1.md`;
- `../CRIPTA_ARCHITECTURE_RULES_RU_V1.md`;
- `PROJECT_ARCHITECTURE_RU.md`;
- `PROJECT_GOVERNANCE_RU.md`;
- `MAYAK_ARCHITECTURE_PRINCIPLES_RU.md`;
- `MARKET_CONTEXT_AND_COIN_RATING_ARCHITECTURE_RU.md`;
- `MAYAK_HISTORICAL_SIGNAL_BACKFILL_RU.md`;
- `MAYAK_COMPONENT_RESEARCH_V1_RU.md`;
- `MAYAK_V2_STAGE_RESULTS_RU.md`.

```text
RESEARCH_STAGE             = FRESH_CROSS_ASSET_OOS
MAYAK_TRADING_EFFECT       = NONE
DISPATCHER_TRADING_EFFECT  = NONE
ENTRY_POLICY_CHANGED       = NO
COIN_MARKET_RATING_FITTED  = NO
THRESHOLD_SELECTION        = NO
RETUNING_ON_OOS            = NO
```

## 1. Цель

Проверить на новых для MAYAK component research активах, сохраняются ли заранее
названные причинные эффекты frozen MAYAK V2 components. Этот этап **не** строит
формулу `CoinMarketRating`, не выбирает cutoff и не меняет live Strategy/Entry.

## 2. Seen discovery source заморожен

Авторитетный seen report:

```text
/srv/cripta/research_runs/mayak_final_stage_20260906_617d/final_component_report/
```

Контрольные SHA256 до открытия OOS:

```text
COMPONENT_SUMMARY.csv
= da6a913ae403939b7136d180a7e3966a5bec0d3139f5d27c00180bc150a9c6ff

QUARTILE_DIAGNOSTICS.csv
= 238f91d477fda5b04326c0a6dbc42bcda5b24457361054bcdb984cca6cdce9ad
```

`HOLDOUT7_DIAGNOSTIC_REUSE` не переименовывается в OOS. Seen ALL9 не используется
для выбора новых признаков после открытия NEW15.

## 3. Cross-asset OOS universe

Тестовые активы заморожены до результата:

```text
AAVEUSDT
APTUSDT
ARBUSDT
AVAXUSDT
BCHUSDT
BNBUSDT
DOTUSDT
HBARUSDT
INJUSDT
LTCUSDT
NEARUSDT
OPUSDT
SUIUSDT
TRXUSDT
XLMUSDT
```

Это `NEW15` из уже существующего universal Entry holdout. Они не входили в
MAYAK component research ALL9.

BTCUSDT и ETHUSDT допускаются только как `REFERENCE_ONLY` внутри replay panel для
market breadth / relative-to-BTC / relative-to-ETH. Их outcomes не входят в OOS.

```text
TEST_OUTCOMES      = NEW15 only
REFERENCE_ONLY     = BTCUSDT + ETHUSDT
REPLAY_PANEL       = NEW15 + BTCUSDT + ETHUSDT
EXPECTED_SIGNALS   = 14024
```

## 4. Frozen Entry identity

NEW15 создан тем же `universal-entry-15m5m-v1` generator, который воспроизводит
frozen ALL9 Entry. До OOS проверено на старых UNI/LINK:

```text
UNIUSDT  113/113 keys identical, DIFF=0
LINKUSDT 114/114 keys identical, DIFF=0
```

Для NEW15 до открытия MAYAK result:

```text
baseline rows              = 14024
unique symbol+touch_at      = 14024
unique symbol+direction+T   = 14024
collisions                  = 0
```

Независимый `independent_entry_search` здесь не используется: это другая Entry
гипотеза.

## 5. Frozen outcomes

Проверяются два тех же исхода:

```text
PLUS_010_VS_MINUS_100
= цена достигла +0.10% раньше исходного -1.00%

PLUS_110_VS_MINUS_100
= цена достигла +1.10% раньше исходного -1.00%
```

Источники NEW15 уже существовали до этого протокола:

```text
/srv/cripta/reports/universal_entry_path_replay_v1/NEW15_FULL_20260824
/srv/cripta/reports/universal_entry_path_replay_v1/NEW15_NO_FLOOR_FULL_20260824
```

Контрольные pool SHA256:

```text
NEW15_FULL/POOL_STATUS.json
= d81b60e0936e694ed1d5fd52f0d621099bdf7320f6cbce75db8e39c140a78ef1

NEW15_NO_FLOOR_FULL/POOL_STATUS.json
= e600fb5d5fb3cf05404dd1a74cf0e1a32ce157ceb0681c1ed69be5f09cfe834a
```

Детерминированный input adapter обязан доказать exact key equality между Entry,
floor и no-floor reports. Он не имеет права подбирать или переопределять outcome.

## 6. Frozen candidate registry

Никакие новые «лучшие» признаки после просмотра NEW15 не добавляются в этот run.

### 6.1 Раннее переживание initial stop: +0.10 раньше -1.00

Ожидаемое направление `HIGHER_IS_GOOD` для всех шести:

```text
basis_mark_index_premium_pct
derivatives_15m_large_trade_share
relative_60m_panel_median_return_pct
liquidity_ask_change_5m_pct
positioning_open_interest_acceleration_5m_pct_per_min2
spot_60m_return_pct
```

### 6.2 Полное продолжение: +1.10 раньше -1.00

`HIGHER_IS_GOOD`:

```text
entry_aligned::market_median_return_5m_pct
derivatives_15m_acceleration_usd_per_min2
derivatives_15m_net_usd
spot_1m_net_usd
```

`LOWER_IS_GOOD`:

```text
entry_aligned::positioning_long_short_imbalance
entry_aligned::liquidity_depth_10bps_imbalance
```

Последние два эффекта намеренно проверяются в inverse-направлении, зафиксированном
по seen report. Их нельзя перевернуть после OOS.

## 7. Frozen quartile transfer

NEW15 **не пересчитывает свои квартильные границы**.

Для каждого кандидата Q1/Q2/Q3 берутся только из seen ALL9
`QUARTILE_DIAGNOSTICS.csv` с контрольным SHA выше и переносятся на NEW15 без
изменения.

Это отдельная защита от same-sample optimization.

## 8. Предварительно зафиксированный confirmation rule

Параметры фиксируются до OOS и не меняются после результата:

```text
MIN_NUMERIC_COVERAGE          = 0.90
MIN_RESOLVED_TOTAL            = 500
MIN_ASSET_GOOD                = 20
MIN_ASSET_BAD                 = 20
MIN_ELIGIBLE_ASSETS           = 8
MIN_SAME_DIRECTION_ASSET_RATE = 0.60
MIN_DIRECTIONAL_AUC           = 0.52
MAX_REJECT_DIRECTIONAL_AUC    = 0.48
MIN_TRANSFER_Q_ENDPOINT_N     = 50
```

Для `LOWER_IS_GOOD` используется directional AUC = `1 - raw AUC`, поэтому
`directional_auc > 0.5` всегда означает движение в заранее ожидаемую сторону.

### CONFIRMED

Одновременно:

1. data gate пройден;
2. directional AUC >= 0.52;
3. медиана good/bad имеет заранее ожидаемый знак;
4. минимум 60% пригодных активов имеют тот же знак AUC;
5. перенесённые seen Q1/Q4 имеют ожидаемый знак outcome-rate.

### REJECTED

Одновременно:

1. data gate пройден;
2. directional AUC <= 0.48;
3. медиана имеет противоположный знак;
4. минимум 60% пригодных активов имеют противоположный знак;
5. transferred Q1/Q4 также имеют противоположный знак.

### MIXED

Data gate пройден, но полного набора условий `CONFIRMED` или `REJECTED` нет.

### INSUFFICIENT_DATA

Недостаточна numeric coverage, resolved sample, число пригодных активов или
endpoint coverage frozen quartiles.

Эти четыре статуса являются research verdict, а не торговыми командами.

## 9. Causal source contract

Базовый market/derivatives replay обязан использовать тот же:

```text
raw archived event
-> CausalMayakReplay
-> LiveMayakEngine
-> snapshot at signal T
```

и только events `event_at <= T`.

Дополнительные Spot/OI/funding/basis/account-ratio/orderbook слои используют те же
замороженные feature definitions и causal availability rules, что completed MAYAK
V2 stage. Missing data остаётся missing/NO_DATA, а не превращается в ноль.

## 10. Output

Минимум:

```text
OOS_CANDIDATE_RESULTS.csv
OOS_ASSET_RESULTS.csv
OOS_FROZEN_QUARTILE_TRANSFER.csv
RUN_MANIFEST.json
```

Manifest обязан хранить source commit, hashes всех входов, seen report hashes,
exact NEW15 symbols, reference-only symbols, candidate registry, frozen gates,
feature source manifests и флаги:

```text
threshold_selection=false
retuning=false
coin_market_rating_fitted=false
trading_effect=NONE
```

## 11. Запреты после открытия OOS

После первого чтения NEW15 result в рамках V1 запрещено:

- добавлять новый кандидат в этот run;
- менять направление кандидата;
- менять AUC/coverage/asset/quartile gates;
- пересчитывать квартильные границы на NEW15;
- исключать неудобную монету из NEW15;
- объявлять `MIXED` признак подтверждённым через новый post-hoc cutoff;
- строить `CoinMarketRating` до owner review.

Новый вопрос после OOS требует отдельного V2 research protocol и не переписывает
результат V1.

## 12. Promotion boundary

Даже если несколько компонентов получат `CONFIRMED`:

```text
OOS evidence
-> owner review
-> separate CoinMarketRating research contract
```

Это **не** разрешает автоматически менять Strategy, Entry, Dispatcher consumer
policy или live trading.
