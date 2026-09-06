# MAYAK V2 — ИТОГИ LIVE / HISTORICAL REPLAY / COMPONENT RESEARCH

**Документ:** `MAYAK_V2_STAGE_RESULTS_RU.md`
**Версия:** 1.0
**Дата:** 2026-09-06
**Статус:** доказательный research/evidence report; не trading policy и не формула `CoinMarketRating`

Верхние контракты:

- `MAYAK_ARCHITECTURE_PRINCIPLES_RU.md`
- `MAYAK_OBJECTIVE_CONTEXT_V2_IMPLEMENTATION_RU.md`
- `MAYAK_COMPONENT_RESEARCH_V1_RU.md`
- `MARKET_CONTEXT_AND_COIN_RATING_ARCHITECTURE_RU.md`
- `PROJECT_GOVERNANCE_RU.md`

```text
MAYAK_TRADING_EFFECT = NONE
ENTRY_POLICY_CHANGED = NO
COIN_MARKET_RATING_FITTED = NO
THRESHOLD_SELECTION = NO
RESEARCH_LABEL = DISCOVERY_SEEN_FROZEN_ALL9
```

## 1. Что завершено

MAYAK V2 доведён до objective per-coin context `mayak-v2.2` / `objective-coin-context-v2`, установлен в live observation-mode и проверен отдельным causal historical replay.

Historical research восстановил состояние рынка в точке frozen Entry T только из данных, доступных к T. Entry/PnL/outcome не подавались внутрь MAYAK. Outcome присоединялся внешним Analyst после формирования immutable market context.

Итоговая выборка:

```text
ALL9 frozen Entry signals = 1063
features in frozen component report = 251
component summary rows = 7028
quartile diagnostics rows = 28112
```

Основной итоговый каталог:

```text
/srv/cripta/research_runs/mayak_final_stage_20260906_617d/
```

Ключевые доказательные артефакты:

```text
final_component_report/RUN_MANIFEST.json
SHA256 dc3dbfd983327625ec7e1fae15ba77b3ea0ecbbe451dbffd3ded9dad260acdb3

final_component_report/COMPONENT_SUMMARY.csv
SHA256 da6a913ae403939b7136d180a7e3966a5bec0d3139f5d27c00180bc150a9c6ff

final_component_report/QUARTILE_DIAGNOSTICS.csv
SHA256 238f91d477fda5b04326c0a6dbc42bcda5b24457361054bcdb984cca6cdce9ad

ORDERBOOK_ALL9_MANIFEST.json
SHA256 b8adb5d8738225a85f49f657e7077ff7db05c08e315ad34debc7c7eb1ce7f030

MAYAK_ORDERBOOK_FEATURES_ALL9.csv
SHA256 7a837f6f280d12efc4db10181ad5f352996f2954fe6346d2922d62b28faea97e
```

## 2. Покрытие источников

В frozen historical comparison доказаны следующие causal слои:

```text
derivatives executed public trades       EXACT / 1063
Spot executed public trades              EXACT / 1063
Open Interest 5/15/30/60m                EXACT / 1063
funding                                   EXACT / 1063
mark/index/premium                        EXACT / 1063
long/short account ratio                 EXACT / 1063
derivatives depth-200 orderbook          EXACT / 1063
price / breadth / relative strength       EXACT / 1063
```

Для exact depth-200 использован `snapshot + delta` reconstruction с fail-closed update-id continuity и causal cutoff на границах UTC суток. Для SOL был найден реальный архивный boundary-case: файл предыдущего дня физически содержал события следующего дня после frozen touch. Runner исправлен так, чтобы previous-day pre-roll никогда не читал market state позже T. Реальный acceptance дал:

```text
touch_at          = 2026-06-21T00:00:00.208500Z
last book state   = 2026-06-21T00:00:00.113000Z
current/previous/1m/5m/15m baselines = PRESENT
```

Исторически недоступные exact слои не подменялись proxy:

```text
old exact liquidations before raw capture = NO_DATA
historical external event/news context     = NO_DATA / no-source
```

## 3. Реальные исходы frozen Entry

Это реальные последующие price-path outcomes frozen exact-touch сигналов, а не утверждение о фактических биржевых fills конкретного live-счёта.

### 3.1 Раннее выживание

```text
+0.10% before -1.00% = 995
-1.00% before +0.10% = 66
data end             = 2
resolved success     = 93.779%
```

Следовательно, сам Entry очень часто переживает первый шум. MAYAK здесь решает более редкую задачу — отличить 66 ранних аварий от основной массы.

### 3.2 Продолжение движения

```text
+1.10% before -1.00% = 594
-1.00% before +1.10% = 460
data end             = 9
resolved success     = 56.357%
```

По направлениям:

```text
LONG  = 52.008% reached +1.10 before -1
SHORT = 60.640% reached +1.10 before -1
```

По монетам resolved `+1.10/-1` success ranged примерно от 50.4% XRP до 67.8% SOL. Это дополнительно запрещает считать pooled результат универсальной Strategy policy без cross-asset проверки.

## 4. Главный ответ: насколько MAYAK правильно видит рынок

### 4.1 Как объективный наблюдатель — PASS

Несколько независимых физических каналов, рассчитанных причинно в T, различают будущие outcomes лучше случайного порядка. Эффекты видны не только pooled ALL9, но у ряда признаков сохраняют знак на 7–9 из 9 активов.

Это означает, что состояние рынка в момент Entry действительно содержит полезную информацию, и MAYAK V2 эту информацию способен фиксировать до исхода сделки.

### 4.2 Как готовый единый “хорошо/плохо” рейтинг — НЕ ДОКАЗАНО

`CoinMarketRating` намеренно не фитился. Ни один компонент сам по себе не даёт достаточно сильной separation, чтобы объявить его trading gate.

Старое агрегированное поле `mayak_confidence` практически не разделяет outcomes:

```text
AUC +0.10/-1  ≈ 0.509
AUC +1.10/-1  ≈ 0.500
```

Следовательно, старый confidence не является адекватной итоговой оценкой рынка. Ценность MAYAK V2 находится в объективной многокомпонентной карточке, а не в этом старом числе.

## 5. Что MAYAK видел перед ранней аварией `-1%`

Для `+0.10/-1` сильнейшие univariate differences на seen sample:

| Физический блок | Признак | ALL9 AUC | DEV2 | H7 diagnostic | Одинаковый знак по монетам |
|---|---|---:|---:|---:|---:|
| Funding / basis | `mark_index_premium_pct` | 0.596 | 0.573 | 0.592 | 6/9 |
| Derivatives flow | `15m_large_trade_share` | 0.584 | 0.644 | 0.571 | 6/9 |
| Market regime | `60m_panel_median_return_pct` | 0.582 | 0.483 | 0.614 | 8/9 |
| Orderbook | `ask_change_5m_pct` | 0.581 | 0.584 | 0.579 | 7/9 |
| OI | `OI acceleration 5m` | 0.576 | 0.663 | 0.550 | 6/9 |
| Spot | `60m_return_pct` | 0.575 | 0.470 | 0.614 | 8/9 |

На высокой базовой вероятности 93.8% абсолютные quartile differences ожидаемо небольшие, но причинно заметны. Например:

```text
basis premium:            Q1 92.1% -> Q4 96.6% early survival
15m large-trade share:    Q1 91.7% -> Q4 95.5%
5m ask-liquidity change:  Q1 91.4% -> Q4 96.6%
OI acceleration:          Q1 91.7% -> Q3/Q4 ~95%
```

Это candidate evidence для раннего market-stress context, а не разрешение автоматически блокировать Entry.

## 6. Что MAYAK видел перед полноценным `+1.10%` продолжением

Для более важного outcome `+1.10/-1` separation слабее, но несколько признаков заметно устойчивее между активами.

### 6.1 Общий рынок в направлении Strategy

`entry_aligned::market_median_return_5m_pct`:

```text
ALL9 AUC = 0.556
DEV2     = 0.572
H7 diag  = 0.551
same sign = 9/9 coins
```

Same-sample quartiles:

```text
worst quartile -> 49.6% reached +1.10 before -1
best quartile  -> 59.8%
```

Это наиболее чистое cross-asset evidence: когда весь рынок за последние 5 минут меньше противоречит направлению Strategy, дальнейший ход Entry чаще продолжается.

### 6.2 Derivatives executed flow

Raw 15m acceleration:

```text
ALL9 AUC = 0.558
same sign = 7/9 coins
Q1 success = 51.9%
Q4 success = 62.9%
```

Raw 15m net flow:

```text
Q1 success = 52.3%
Q4 success = 62.1%
```

Однако простая Strategy-specific проекция `LONG=buy good / SHORT=sell good` слабая: лучшие direction-adjusted executed-flow AUC находятся примерно в диапазоне 0.52–0.53 для `+1.10/-1`.

Это важный архитектурный результат: **MAYAK правильно хранит executed money as objective fact, но сам не должен интерпретировать этот поток как пригодность конкретного Entry.** Frozen Entry может быть reversal/touch, и в хороший момент входа рынок ещё способен исполнять сделки против будущего направления.

## 7. Spot подтверждает независимую ценность реального денежного потока

Spot и derivatives исследованы отдельно. Spot не оказался дубликатом derivatives.

Например `spot_1m_net_usd` на seen sample:

```text
ALL9 AUC ≈ 0.545
same sign ≈ 7/9 coins
Q1 +1.10 success ≈ 48.3%
Q4 +1.10 success ≈ 61.0%
```

Форма между квартилями не полностью монотонна, поэтому нельзя выбирать cutoff. Но факт независимой Spot-информации подтверждён и оправдывает отдельный Spot money layer MAYAK.

## 8. Crowding: большинство не равно подтверждению

`entry_aligned::positioning_long_short_imbalance` для `+1.10/-1`:

```text
ALL9 AUC = 0.437
same inverse sign = 7/9 coins
```

Quartiles:

```text
crowd more opposite Entry -> 64.8% success
crowd more aligned Entry  -> 46.2% success
```

Это не торговое правило “идти против толпы”. Это evidence, что account-ratio является crowding/stress context, а не подтверждением направления.

Следствие для MAYAK: long/short ratio необходимо хранить как objective positioning fact и не преобразовывать внутри MAYAK в LONG/SHORT command.

## 9. Стакан: опубликованная ликвидность не равна деньгам

`entry_aligned::liquidity_depth_10bps_imbalance`:

```text
ALL9 AUC = 0.444
same inverse sign = 8/9 coins
```

Quartiles:

```text
less displayed depth aligned with Entry -> 61.4% +1.10 success
more displayed depth aligned with Entry -> 47.7% +1.10 success
```

Это подтверждает архитектурный принцип проекта: orderbook — posted liquidity, а не доказательство вошедших денег. Большая видимая “поддержка” по стороне Entry не обязана означать лучший будущий price path и может отражать crowding/passive liquidity.

## 10. Конкретный пример: что было с Entry и что MAYAK видел в T

Иллюстрация, а не выбранное правило: два frozen `LINKUSDT LONG`.

### Победитель

```text
touch:       2026-05-18 10:01:42 UTC
outcome:     +1.10% before -1%
+1.10 event: 2026-05-18 11:41:10 UTC
```

MAYAK facts at T:

```text
5m market median return        -0.096%
derivatives 15m net            -79k USD
Spot 1m net                    +1.55k USD
10bps book imbalance           +0.055
```

### Проигравший

```text
touch:       2026-05-22 18:04:17 UTC
outcome:     -1% before +1.10%
-1 event:    2026-05-22 18:37:26 UTC
```

MAYAK facts at T:

```text
5m market median return        -0.201%
derivatives 15m net            -474k USD
Spot 1m net                    -8.55k USD
10bps book imbalance           -0.138
```

В этой паре проигравший Entry уже в T находился в существенно более тяжёлой среде сразу по нескольким независимым каналам. Но отдельные пары бывают противоположными общей статистике, поэтому один snapshot нельзя превращать в детерминированный verdict.

## 11. Старый текстовый `mayak_state`

Legacy/partial historical state имел некоторую descriptive separation:

```text
переходный рынок        ~62.1% +1.10 success
спокойный рынок         ~60.7%
направленное движение   ~56.6%
денежное расхождение    ~53.4%
синхронный пролив       ~44.7%
синхронный вынос вверх  ~35.7%
```

Но этот label был сформирован до полного historical enrichment Spot/OI/orderbook и не является Strategy-aware. Его нельзя использовать как готовый `CoinMarketRating`.

## 12. Архитектурные выводы

Research подтвердил несколько ранее принятых архитектурных решений:

1. **MAYAK должен оставаться strategy-agnostic.** Direction-adjusted money flow слабее objective/raw regime facts; смысл потока зависит от механики Strategy.
2. **Dispatcher не должен знать тип Entry.** Только Strategy имеет право решить, является ли конкретное состояние рынка подходящим для её reversal/breakout/другой механики.
3. **Spot и derivatives должны храниться отдельно.** Они дают независимую информацию.
4. **Orderbook не является executed money.** Displayed liquidity может иметь противоположный смысл.
5. **Crowding не равно подтверждению.** Account-ratio нужен как отдельный positioning context.
6. **Один confidence/state недостаточен.** Нужна объективная многокомпонентная карточка монеты/рынка.

## 13. Что пока НЕ доказано

Этот run не является свежим OOS:

```text
DEV2 = UNI + LINK
HOLDOUT7 = diagnostic reuse only
ALL9 already seen
```

Следовательно, запрещено утверждать по этому run:

- финальную формулу `CoinMarketRating`;
- оптимальный cutoff любого компонента;
- trading veto/allow;
- автоматический Entry filter;
- portfolio PnL uplift;
- универсальность между будущими периодами/новыми активами.

Дополнительные ограничения:

- ранних `-1 before +0.10` всего 66;
- exact historical liquidations отсутствуют для frozen периода;
- historical external event context отсутствует;
- multi-exchange/on-chain/external flow пока не входит в этот replay.

## 14. Итоговый verdict MAYAK V2 stage

```text
OBJECTIVE_LIVE_CONTEXT             = PASS
LIVE_SPOT_COVERAGE_20_20           = PASS
CAUSAL_LIVE_REPLAY_EQUIVALENCE     = PASS
DERIVATIVES_HISTORICAL_CONTEXT     = PASS
SPOT_HISTORICAL_CONTEXT            = PASS
OI_HISTORICAL_CONTEXT              = PASS
FUNDING_BASIS_CONTEXT              = PASS
ACCOUNT_RATIO_CONTEXT              = PASS
DEPTH200_LIQUIDITY_CONTEXT         = PASS
FROZEN_ENTRY_EXTERNAL_CORRELATION  = PASS
COMPONENT_RESEARCH                 = PASS

MAYAK_TRADING_EFFECT               = NONE
COIN_MARKET_RATING_FITTED          = NO
ENTRY_POLICY_CHANGED               = NO
FRESH_OOS_CONFIRMATION             = PENDING
```

Смысл результата: **MAYAK V2 уже достаточно хорошо видит объективную рыночную обстановку, чтобы его данные имели измеримую связь с реальными последующими outcomes Entry. Но он ещё не доказан как готовый агрегированный предсказатель или торговый gate.**

## 15. Следующий research gate

Следующий научно корректный шаг:

```text
new temporal / cross-asset data not used in this research
-> same frozen component definitions
-> no retuning on new data
-> confirmation / rejection of candidate effects
-> owner review
-> only then separate Dispatcher CoinMarketRating research contract
```

До прохождения этого шага live Strategy/Entry не меняются.
