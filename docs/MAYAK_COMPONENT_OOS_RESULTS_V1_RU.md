# MAYAK V2 — ИТОГИ CROSS-ASSET OOS FROZEN COMPONENTS V1

**Документ:** `MAYAK_COMPONENT_OOS_RESULTS_V1_RU.md`
**Версия:** 1.0
**Дата:** 2026-09-07
**Статус:** LEVEL 5 · итоговый research/evidence report; не trading policy и не формула `CoinMarketRating`

Верхние контракты:

- `../CRIPTA_ASSISTANT_WORK_RULES_RU_V1.md`;
- `../CRIPTA_ARCHITECTURE_RULES_RU_V1.md`;
- `PROJECT_ARCHITECTURE_RU.md`;
- `PROJECT_GOVERNANCE_RU.md`;
- `MAYAK_ARCHITECTURE_PRINCIPLES_RU.md`;
- `MARKET_CONTEXT_AND_COIN_RATING_ARCHITECTURE_RU.md`;
- `MAYAK_COMPONENT_OOS_CONFIRMATION_V1_RU.md`.

```text
RESEARCH_STAGE             = FRESH_CROSS_ASSET_OOS
MAYAK_TRADING_EFFECT       = NONE
DISPATCHER_TRADING_EFFECT  = NONE
ENTRY_POLICY_CHANGED       = NO
COIN_MARKET_RATING_FITTED  = NO
THRESHOLD_SELECTION        = NO
RETUNING_ON_OOS            = NO
```

## 1. Что проверено

Frozen protocol V1 проверен на заранее зафиксированных `NEW15` без изменения списка активов, кандидатов, направлений, квартильных границ и confirmation gates после открытия результата.

```text
TEST_OUTCOMES      = NEW15 only
REFERENCE_ONLY     = BTCUSDT + ETHUSDT
REPLAY_PANEL       = NEW15 + BTCUSDT + ETHUSDT
SIGNALS            = 14024
FROZEN_CANDIDATES  = 12
```

Полный causal replay использовал тот же `LiveMayakEngine`, что и live observation path. Все дополнительные слои были присоединены только причинно к `signal T`:

- derivatives executed flow;
- Spot executed flow;
- Open Interest;
- funding + mark/index premium;
- account long/short ratio;
- exact derivatives depth-200 liquidity;
- market breadth / relative context.

Перед открытием OOS был выполнен полный resource/data gate:

```text
MAYAK_RESOURCE_SMOKE=PASS signals=14024 candidates=12
```

## 2. Outcome coverage

### 2.1 Раннее переживание исходного stop

```text
+0.10% раньше -1.00% = 13083
-1.00% раньше +0.10% =   927
unresolved              =    14
resolved total          = 14010
```

### 2.2 Полное продолжение

```text
+1.10% раньше -1.00% = 6575
-1.00% раньше +1.10% = 7234
unresolved              =  215
resolved total          = 13809
```

## 3. Итог OOS

```text
CONFIRMED         = 1
MIXED             = 11
REJECTED          = 0
INSUFFICIENT_DATA = 0
```

Ни один из шести frozen-кандидатов для исхода `+0.10% раньше -1.00%` не прошёл полный confirmation rule. Все шесть получили `MIXED`.

Из шести кандидатов для исхода `+1.10% раньше -1.00%` подтвердился один; остальные пять получили `MIXED`.

## 4. Единственный подтверждённый эффект

Подтвердился объективный positioning/crowding-факт:

```text
feature             = entry_aligned::positioning_long_short_imbalance
outcome             = PLUS_110_VS_MINUS_100
expected_direction  = LOWER_IS_GOOD
verdict             = CONFIRMED
```

В пользовательском смысле это означает:

> Для frozen Entry вероятность дойти до +1.10% раньше -1.00% была выше, когда account long/short crowding было **меньше выстроено в сторону нашего Entry**. Это подтверждает полезность long/short ratio как объективного датчика перегруженности/позиционирования, но не создаёт торговое правило «идти против толпы».

Полный NEW15:

```text
numeric coverage              = 100.0%
directional AUC               = 0.5322453959
median successful outcomes    = -0.1894
median failed outcomes        =  0.2248
eligible assets               = 15
same frozen-direction assets  = 12 / 15 = 80.0%
opposite-direction assets     = 3 / 15
```

Перенос frozen ALL9 quartile cuts без пересчёта на NEW15:

```text
Q1  n=2100  +1.10-before--1 rate = 53.3333%
Q4  n=2117  +1.10-before--1 rate = 44.4969%
absolute Q1-Q4 difference       = +8.8364 percentage points
```

Три актива имели противоположный знак относительно frozen expectation:

```text
BNBUSDT
OPUSDT
TRXUSDT
```

Остальные 12 имели ожидаемый знак.

## 5. Что НЕ подтвердилось как frozen effect

Следующие кандидаты прошли data gate, но не весь заранее заданный confirmation rule, поэтому их статус остаётся `MIXED`, а не «почти подтверждён»:

### +0.10% раньше -1.00%

- basis / mark-index premium;
- доля крупных derivatives-сделок за 15 минут;
- 60-минутная медиана движения панели;
- изменение ask-liquidity за 5 минут;
- ускорение OI за 5 минут;
- 60-минутное Spot price movement.

### +1.10% раньше -1.00%

- 5-минутное движение рынка в направлении Entry;
- 15-минутное ускорение derivatives executed flow;
- 15-минутный net derivatives executed flow;
- 1-минутный Spot net executed flow;
- depth-10bps liquidity imbalance в направлении Entry.

`MIXED` не разрешено превращать в `CONFIRMED` подбором нового cutoff на NEW15.

## 6. Покрытие данных

Все кандидаты прошли frozen data gate. У 11 из 12 numeric coverage = 100%.

Для `spot_1m_net_usd`:

```text
numeric coverage = 90.2310%
```

Это выше заранее замороженного порога 90%, поэтому статус не является `INSUFFICIENT_DATA`.

## 7. Ключевые доказательные артефакты

Авторитетный result root:

```text
/srv/cripta/research_runs/mayak_component_oos_new15_v1_20260906_7b366ea/oos_result_v1/
```

SHA256:

```text
OOS_CANDIDATE_RESULTS.csv
= 6de50bb25f2047918f3ca5abb0a626748a610eddf247b13ae6d957b0941092dd

OOS_ASSET_RESULTS.csv
= ba8377be07d842995893b656aac9b6d82b450315da05fbf84c3d43d6045b135d

OOS_FROZEN_QUARTILE_TRANSFER.csv
= 32637b23e03fa7956938f5799cff275e2175fb60ba95d61b250c0ef97492b742

RUN_MANIFEST.json
= d4b678f3cf4971b0d60ac39e7af4f9cafeb6df6dc32e707d141740ce589f96d3
```

Merged exact orderbook layer:

```text
rows = 14024
symbols = 15
exact key equality to base = YES
SHA256 = 08363374b45d716b5aaaa39ace6d75d670fa4eb18bcb281af0a8a30d43f0bd32
```

Seen frozen hashes были проверены evaluator без изменения:

```text
COMPONENT_SUMMARY.csv
= da6a913ae403939b7136d180a7e3966a5bec0d3139f5d27c00180bc150a9c6ff

QUARTILE_DIAGNOSTICS.csv
= 238f91d477fda5b04326c0a6dbc42bcda5b24457361054bcdb984cca6cdce9ad
```

## 8. Запреты после открытия результата

Результат V1 теперь frozen. Запрещено задним числом:

- добавлять кандидатов в этот run;
- менять ожидаемое направление;
- менять AUC/coverage/asset/quartile gates;
- пересчитывать quartile cuts на NEW15;
- исключать неудобные активы;
- объявлять `MIXED` подтверждённым через post-hoc threshold.

Любой новый вопрос требует отдельного research protocol/version.

## 9. Архитектурный вывод

OOS подтверждает, что MAYAK должен продолжать хранить long/short positioning как отдельный объективный факт. Он не подтверждает готовый универсальный `CoinMarketRating`.

```text
COIN_MARKET_RATING_FITTED = NO
DISPATCHER_TRADING_EFFECT = NONE
STRATEGY_POLICY_CHANGED   = NO
```

По pre-registered promotion boundary следующий шаг:

```text
OOS EVIDENCE
-> OWNER REVIEW
-> SEPARATE CoinMarketRating RESEARCH CONTRACT
```

До owner review формула рейтинга не строится и Dispatcher/Strategy consumers не меняются.
