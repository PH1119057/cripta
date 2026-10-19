# CRIPTA — журнал исследований рыночной микроструктуры

**Зафиксировано 2026-10-18.** Данные: **2026-05-19, 00:00–24:00 UTC**. Статус: **RESEARCH EVIDENCE / NOT CANON / NOT LIVE POLICY**.  
**Исследуемая торговая пятёрка:** APTUSDT, INJUSDT, DOTUSDT, LTCUSDT, ARBUSDT (сверено с `docs/TRADING_CONTOUR_RU.md`). TRXUSDT исключён из торгового исследования, может оставаться в панели наблюдения.
**Authority:** GitHub `PH1119057/cripta:main`, текущие canonical docs в `docs/`. Этот журнал не добавляется в Project Source и не меняет канон.

## Для нового чата
Прочитать обязательные canonical docs и `docs/RESEARCH_COMPUTE_RULES_RU.md`; затем **этот журнал**. Цель: инвентаризация и воспроизводимый расчёт **всех 30 кандидатов** для каждой из пяти монет одновременно, собственные причинные нормы, общий рынок, LONG/SHORT отдельно, OOS и результаты **после комиссий**. Не возвращаться к бесконечным однопоказательным исследованиям до закрытия чек-листа. Выполненные в старом чате Python-команды были **одноразовыми, в памяти**: постоянных исходных скриптов или полной матрицы 7200 строк пока не создавали.

## Источники и временные границы
- Book 200 ZIP JSONL: `/data/cripta/datasets/raw/20260518_20260816/{SYMBOL}/orderbook/2026-05-19_{SYMBOL}_ob200.data.zip`; поля `type=snapshot|delta`, `ts`, `cts`, `data.u`, `data.b/a`. Snapshot **заменяет** весь book, не суммирует.
- Trades CSV GZ: `/data/cripta/datasets/raw/20260518_20260816/{SYMBOL}/public_trades/{SYMBOL}2026-05-19.csv.gz`; `timestamp` seconds, aggressor `side`, `price`, `foreignNotional`, `trdMatchID`.
- Liquidations: `/data/cripta/datasets/external/cryptohftdata/bybit_liquidations_v1/raw/2026-05-19/{HH}/{SYMBOL}_liquidations.parquet.zst`. Только наличие, **не** полнота покрытия подтверждены.
- Строгие границы `[2026-05-19T00:00Z, 2026-05-20T00:00Z)`, 1440 минут × 5. Book state берётся **в конце** минуты, после всех delta, переносится через границы часов; отдельные сообщения в начале 20 мая отбрасывать. `cts` и timestamp trades использовать для порядка событий, не смешивать с публикационным `ts`.
- Historical OI, Spot/Perp, cross-venue именно за 19 мая **не подтверждены**. Нет файла ≠ нулевое событие.

## Суточное покрытие, все 5 монет

| Symbol | Book msgs за день | Book минут | Trade rows | Минут со сделками | Оборот USD | Медиана spread bps | Медиана imbalance ±5bps |
|---|---:|---:|---:|---:|---:|---:|---:|
| APT | 606837 | 1440 | 116683 | 1439 | 6.63m | 1.0636 | +0.0235 |
| INJ | 733303 | 1440 | 368454 | 1440 | 57.89m | 2.0103 | -0.0024 |
| DOT | 631778 | 1440 | 143184 | 1438 | 13.96m | 0.8102 | +0.0166 |
| LTC | 542094 | 1440 | 69495 | 1428 | 27.02m | 1.8459 | +0.0484 |
| ARB | 616466 | 1440 | 154123 | 1439 | 12.32m | 0.8656 | -0.0360 |
| **TOTAL** | **3130478** | **7200** | **851939** | — | — | — | — |

При проверке `data.u` 0 разрывов; 3 snapshots на архив, включая границу следующего дня; это не гарантия наличия всех физических событий. Глубина ±5bps относительно **движущегося mid**, чувствительна к дрейфу уровня.

## Рассчитанные факторы исполнения и цены

| Symbol | RVOL p50 (текущая минута / средняя из предыдущих 60m) | RVOL p90 | CVD USD за день | Autocorr 1m lag1 | Median 20m close range % | VWAP day | POC price |
|---|---:|---:|---:|---:|---:|---:|---:|
| APT | 0.419 | 2.488 | +43222 | -0.0360 | 0.3894 | 0.940239 | 0.9385 |
| INJ | 0.497 | 2.069 | -4714464 | -0.0343 | 0.8157 | 5.067505 | 4.91 |
| DOT | 0.559 | 2.516 | -224835 | -0.0336 | 0.3499 | 1.236346 | 1.2274 |
| LTC | 0.292 | 2.074 | +299290 | -0.0464 | 0.2400 | 54.066551 | 54.12 |
| ARB | 0.541 | 2.287 | -458836 | -0.0495 | 0.4004 | 0.115864 | 0.11661 |

Медиана Delta acceleration USD (разность signed delta соседних минут): APT -15.8, INJ -500.09, DOT +61.7, LTC -44.05, ARB -62.27. **Day VWAP/POC — ex post; нельзя использовать на внутридневном моменте без as-of пересчёта.** Полный rolling CVD и rolling Volume Profile ещё не верифицированы.

Range compression prototype = 20m close-range / 100m предшествующего close-range (окна разной длины, структурно занижает отношение). Median: APT 0.3850, INJ 0.3768, DOT 0.3953, LTC 0.4146, ARB 0.3965. Это не финальная self-normalized мера.

Минутные кандидаты Sweep/Reclaim от prior-60m high/low, полный начальный тест `high breaches/reclaims; low breaches/reclaims`:
APT 51/23;64/24, INJ 54/24;86/52, DOT 52/27;60/21, LTC 51/22;52/18, ARB 43/20;74/25. **При изменении начальной границы окна low-значения меняются; не смешивать результаты.** Точная внутриминутная последовательность ещё не подтверждена.

Top-of-book OFI 1h, единиц базовой монеты: APT +18389, INJ +17433.3, DOT -124443.1, LTC +1008.1, ARB -110342.6. Сэмплированный Top1/Top5 `Δbid USD - Δask USD` (каждое 10-е сообщение) был APT +1275/-7298; INJ +298/+2632; DOT +1042/+508; LTC -4076/+11170; ARB -254/-3403. **НЕ строгий multi-level OFI** — изменение цены уровней не обработано корректно.

## Совместные исследования (НЕ OOS, НЕ торговая стратегия)

Условия: 5m неперекрывающиеся start intervals, предыдущее направление цены согласовано с направлением aggressor flow; `continuation/reversal`.

| Coin | Total depth increased | Depth not increased | Increased + rate acceleration |
|---|---:|---:|---:|
| APT | 42/24 | 39/37 | 30/17 |
| INJ | 40/28 | 45/46 | 23/15 |
| DOT | 37/43 | 44/30 | 20/21 |
| LTC | 33/43 | 29/34 | 21/26 |
| ARB | 36/34 | 48/36 | 21/22 |

Раздельно LONG/SHORT, взаимно исключающие состояния `Only Bid / Only Ask / Both / Neither`, продолжение/разворот:

| Coin | LONG Bid | LONG Ask | LONG Both | LONG Neither | SHORT Bid | SHORT Ask | SHORT Both | SHORT Neither |
|---|---|---|---|---|---|---|---|---|
| APT | 12/7 | 14/9 | 8/5 | 8/9 | 14/11 | 9/7 | 7/3 | 9/10 |
| INJ | 20/12 | 11/11 | 7/7 | 8/9 | 9/5 | 14/16 | 6/4 | 10/10 |
| DOT | 16/19 | 15/6 | 5/8 | 3/3 | 17/13 | 15/18 | 4/4 | 6/2 |
| LTC | 18/11 | 11/13 | 3/7 | 3/5 | 9/14 | 10/8 | 3/11 | 5/8 |
| ARB | 11/11 | 14/11 | 3/5 | 6/6 | 19/12 | 16/14 | 7/4 | 8/7 |

**Предупреждение**: один предыдущий расчёт APT некорректно сделал группы перекрывающимися. Выше приведены согласованные disjoint buckets, но нужен независимый QC при переносе в воспроизводимый код. Универсальная закономерность по монетам не подтверждена; выборки малы.

Секундные кандидаты сокращения Bid после SELL / Ask после BUY (первые 15m): APT 39/60, INJ 100/101, DOT 46/44, LTC 12/19, ARB 37/33. По сдвигу book-depth нельзя строго доказать отмены, поглощение или восстановление тех же уровней; `±5bps` пересчитывается от динамического mid.
После введения разреживания событий минимум 30 сек остаётся `SELL no_recovery/recovery; BUY no_recovery/recovery`: APT 4/2;3/9, INJ 3/4;5/7, DOT 0/6;2/9, LTC 1/4;3/4, ARB 1/7;3/5. Пятиминутные future outcomes всё ещё могут пересекаться. Сильные доли вроде INJ BUY 95/101 без разреживания — **псевдорепликация и тренд**, не торговый edge. Контрольные моменты с тем же рыночным контекстом не завершены.

Liquidation file HOURS за день: APT 00,09,11,14,16,17,18; INJ 00,01,02,03,04,05,09,10,11,14,16,17,18,19,22; DOT 14; LTC 00,14; ARB 14,16,22,23. Отсутствие файла НЕ=0 ликвидаций.

## Реестр 30 кандидатов — только статус исследования

`CALC` = вычислялся хотя бы на одной выборке, но не validated/OOS. `PARTIAL` = приближённый показатель или неполное покрытие. `NOT_CHECKED` = нет подтверждённого расчёта. Реестр **не утверждён как canonical strategy feature set**.

| # | Factor | Status | Что ещё проверить |
|---:|---|---|---|
| 1 | RVOL | CALC | multiday causal self-baseline |
| 2 | Trade Rate | CALC | per-coin rolling norms |
| 3 | Trade Rate Acceleration | CALC | horizons and QC |
| 4 | Large Trade Share | PARTIAL | all 5, as-of thresholds |
| 5 | Buy/Sell Delta | CALC | time alignment |
| 6 | CVD | CALC | rolling/as-of |
| 7 | Delta Acceleration | CALC | rolling time series |
| 8 | Trade Streaks | PARTIAL | group split fills properly |
| 9 | Queue Imbalance | CALC | normalize depth |
| 10 | Multi-Level OFI | PARTIAL | true price-aware per-level OFI |
| 11 | Order Book Depth | CALC | depth by side, self-baseline |
| 12 | Pulling Rate | PARTIAL | fills/cancels/price drift |
| 13 | Depth Recovery | PARTIAL | same-level recovery |
| 14 | Price Impact | PARTIAL | signed lag causal impact |
| 15 | Effort / Result | PARTIAL | normalized metric |
| 16 | Absorption | PARTIAL | verified event chain |
| 17 | Exhaustion | NOT_CHECKED | decay of flow/impact |
| 18 | Short-Term Autocorrelation | CALC | lag variants and OOS |
| 19 | Range Compression | PARTIAL | comparable baseline |
| 20 | Local Extremes | CALC | causal rolling reference |
| 21 | Sweep / Reclaim | PARTIAL | distinct intraminute sequence |
| 22 | VWAP / Volume Profile | PARTIAL | live as-of rolling profile |
| 23 | OI Change | NOT_CHECKED | historical coverage |
| 24 | Liquidation Spikes | PARTIAL | file coverage QC + values |
| 25 | Spot / Perpetual Divergence | NOT_CHECKED | spot archive May19 |
| 26 | Cross-Venue Lead–Lag | NOT_CHECKED | venue coverage and latency |
| 27 | Inter-Trade Duration | PARTIAL | across all 5 coins |
| 28 | Trade-Size Distribution | PARTIAL | rolling quantiles |
| 29 | Bid/Ask Spread | CALC | rolling distribution |
| 30 | Replenishment / Resiliency | PARTIAL | same-level causal replenish |

## Continuation and safeguards

1. Before changes, check current `main` and relevant research / release / security contracts; source of truth remains canonical `docs/`. Journal is evidence, not authority.
2. Build stable, **read-only, version-controlled calculators** in existing research layout, reusing ingestion and one parsed book/trade stream across 30 physically distinct metrics; don't create 30 redundant source readers. Pin algorithm version, source paths, data coverage, input hashes, symbol/time window and UTC conventions. Same code for 5 min, 1h, day, multi-day. Do not simply copy ephemeral snippets without auditing known mistakes.
3. No server filesystem mutation before exact actor/path/owner/group/mode/ACL/systemd and canonical `CURRENT_PROJECT_MAP §1.4`, `DEVELOPMENT_RELEASE §5.1, §19.1–19.4`; no DB writes, no silent LIVE.
4. Finish unavailable partial metrics and coverage OI/liquidations/Spot/venues with explicit `NO_DATA` (not zero). Validate causality, event deduplication, self-baselines, market control, LONG/SHORT separately, OOS and returns **после комиссий**.
5. This research journal does not authorize new thresholds, signal parameters, Strategy versions, production deployment or MAYAK behavior changes; require explicit owner decision and canonical pathway.


## GitHub reproducible source checkpoint — 2026-10-18

- `research/microstructure/experiments/public_trades_day_v1.py` — первый версионированный **read-only** stdlib Python калькулятор исторических Public Trades. Параметры `--symbol`, `--date`, `--minutes`, `--root`; единый код для пяти монет, первых 5/60 минут и полного дня; вывод JSON в stdout.
- Рассчитывает count/trade-minute coverage, USD notional, signed Delta/CVD, RVOL относительно предшествующих 60 минут, Delta acceleration, 1m autocorrelation lag1, 20m close range, day/window VWAP и volume POC. Последние два — **ex post**, не causal intraday values.
- Это **новая проверяемая реализация**, а не байт-точная копия всех однократных команд предыдущего чата. Сравнение чисел с журналом, unit tests, file hashes, performance/long-range validation **NOT CHECKED HERE**. Качество и идентичность результатов ещё должны быть проверены до масштабного повторного запуска.
- Пока **НЕ сохранены как утверждённые калькуляторы**: Multi-Level OFI, точный Sweep/Reclaim, absorption/replenishment и остальные неполные пункты реестра. Экспериментальные исходники не означают появление общего агрегированного слоя. GitHub commit calculator: `ce2b01a4db619448a00137467a0f650a46893844`.


## 2026-10-18 — reproducibility and execution-distribution checkpoint

- `research/microstructure/experiments/public_trades_day_v1.py`: re-read exact published source from GitHub and executed in-memory under `cripta` against all five 2026-05-19 archives; verified fill counts, CVD, RVOL median, ex-post VWAP, 1m autocorrelation matched journal at stated rounding. **REPRODUCIBILITY SMOKE=PASS** for those values only, not OOS/canonical validity.
- New published source `research/microstructure/experiments/trade_distribution_v1.py` commit `82a40a432afee541085bb74f3a676fa8e234a685`. Read-only stdlib, symbol/date/minutes parameters. Source fetched from GitHub and executed under `cripta` for same five source archives.
- Results `symbol: executions; fraction adjacent fills same timestamp; fill-notional P99 USD; share of total notional in fills >=P99`:
  - APT: 116683; 0.745; 697.65; 0.2109
  - INJ: 368454; 0.737; 1728.77; 0.2651
  - DOT: 143184; 0.758; 1040.40; 0.1977
  - LTC: 69495; 0.786; 3912.15; 0.1685
  - ARB: 154123; 0.749; 833.84; 0.2308
- P95 fill-notional USD: APT 309.99, INJ 736.81, DOT 500.38, LTC 2091.35, ARB 425.56. Median *positive* interval between distinct timestamps (ms): APT 721.4, INJ 75.9, DOT 319.5, LTC 1715.9, ARB 264.8.
- **Physical limitation**: exchange fills sharing a timestamp cannot be treated as independent taker orders. P99 full-window quantile is *ex post*, not causal. Long-window/OOS tests still pending. Research-only source; no shared aggregate layer, DB or production mutation.


## 2026-10-18 — causal large trade share + grouped trade streaks

Published reproducible calculator: `research/microstructure/experiments/large_trade_streaks_v1.py`, GitHub commit `6db162295945f3612b7a0bb94c9f6178a0ec6982`. Published source fetched from GitHub and independently re-executed in-memory as `cripta`; outputs match below, read-only, no PostgreSQL.

Definitions:
- Large Trade Share for minute i: notional proportion of fills >= P99(fill notional) from **previous** 60 minutes; require >=100 historical fills. This threshold is causal as-of minute start. P99 is fill notional, not aggregated parent-order notional.
- Trade Streak: consecutive timestamp+side groups (adjacent fills with identical timestamp and side are combined). Grouping **does not prove** a single parent order. Streak count is number of consecutive groups in the same direction.
- Results `symbol: groups, median streak, p90 streak, max streak, median large trade share, p90 large trade share, eligible minutes`:
  - APT: 29730; 2; 8; 51; 0.0000; 0.3997; 1379
  - INJ: 97097; 2; 8; 124; 0.0809; 0.3816; 1380
  - DOT: 34659; 3; 10; 51; 0.0000; 0.4241; 1378
  - LTC: 14917; 2; 7; 34; 0.0000; 0.2677; 1368
  - ARB: 38743; 3; 9; 59; 0.0000; 0.4534; 1379
- Checklist change: #4 Large Trade Share and #8 Trade Streaks now `CALC` **for this one day**; physical-order reconstruction and OOS remain `NOT VERIFIED`.
- Next open items: #10 strict Multi-Level OFI, #12 Pulling Rate, #17 Exhaustion, then #23 OI coverage, #24 liquidation coverage, #25 Spot/Perp and #26 cross-venue. No Strategy/LIVE policy changes.


## 2026-10-18 — first-hour rank OFI, visible reductions, preliminary exhaustion

Published read-only calculator `research/microstructure/experiments/rank_ofi_v1.py` commit `e867338e54b0217de4bed6f04cdd498b9247f290`; fetched GitHub source, re-executed under cripta for all 5, outputs reproduced. First hour 2026-05-19 UTC:
- APT OFI levels 1..5 (base units): +14496.6, +30394.6, -129058.0, -40390.8, -30382.7; same best-price volume reductions bid/ask 474439.7/485542.1.
- INJ: +17433.8, +518945.9, +354063.7, +272038.7, +298629.9; reductions 182311.5/153656.1.
- DOT: -124430.5, -235591.1, -338504.5, -302770.2, -256348.0; reductions 681553.6/776799.8.
- LTC: +1038.1, +4613.0, +1078.4, +10388.3, +10224.5; reductions 44471.0/37913.2.
- ARB: -110342.6, -947012.9, -921174.6, -1798559.4, +1160540.2; reductions 2869763.9/3521381.7.
OFI = conditional quantity delta on current and prior price per rank, aggregated all book updates. Caveat: deeper rank re-indexing can contaminate interpretation; therefore **#10 PARTIAL**, not validated multi-level OFI. Visible top-price reductions conflate fills + cancellations; **#12 PARTIAL**, NOT Pulling Rate.

Exhaustion prototype: prior 5-minute price change in agreement with latest 5-minute signed trade flow; both successive 5-minute flows same sign; latest absolute flow <50% preceding = candidate for exhaustion. Non-overlapping starting anchors stepped 5m, future 5m price direction measured. Reversal count `decay / non-decay`:
- APT: 14/27 versus 29/53;
- INJ: 11/19 versus 30/70;
- DOT: 19/30 versus 28/58;
- LTC: 15/29 versus 27/44;
- ARB: 14/31 versus 33/59.
No consistent directional advantage across coins; same-day descriptive only; exhaustion threshold arbitrary, **#17 PARTIAL**, not a proven rule. Prototype executed in-memory, source not yet committed. No OOS/fees, no Strategy changes.


## 2026-10-18 — explicit owner research priority: ONE EXCHANGE FIRST

**OWNER DECISION (research sequencing, no strategy/canon trading policy change):** investigate and complete the 30 indicators using **Bybit sources only** first. **Cross-venue / other exchanges is the very last research stage**, to be revisited only after one-exchange indicators, coverage, causal/QC and assessment are completed. Do not spend current runs searching other exchanges or their archives.

Read-only evidence CHECKED HERE:
- Bybit historical raw first-five symbols under `/data/cripta/datasets/raw/20260518_20260816/{SYMBOL}/` each contain `orderbook/` and `public_trades/` directories. No additional OI/Spot directory was found in those exact per-symbol raw locations; that alone does NOT prove missing OI/Spot elsewhere.
- Existing research `/data/cripta/research/runs/l53_marketstate_liq_spot_15coins_2w_20260602_0616` found. Name and files are evidence of earlier separate-period work, NOT historical May 19 raw Spot/OI coverage.
- Liquidation parquet.zst files are present for hours in `research/microstructure/coverage_2026-05-19.md`. Python actor `cripta` currently lacks `pyarrow`, `zstandard`, `pandas`, `duckdb` modules; content/schema decoding NOT CHECKED HERE; do not install dependencies without approved compute preflight.
- Further order: Bybit historical OI, Spot, liquidation semantics/coverage; complete 30-indicator registry using actual available one-exchange data; multi-period validation and results after fees; **cross-venue last**. Missing file != zero observation.


## 2026-10-18 — OWNER DIRECTIVE: stop repeated file hunts, build joint 30-factor research

**OWNER DECISION / RESEARCH SEQUENCING:** preserve every discovered historical data path, existence test, available date range, reader/parser and missingness in THIS journal and `research/microstructure/coverage_2026-05-19.md`. Investigate Bybit one exchange first; other exchanges absolutely last. If required May 19 evidence cannot be located, perform bounded one-day Bybit availability/backfill feasibility check rather than repeatedly searching all server trees. No download or server write until exact actor/path/source/provenance and research contracts preflight. Historical API retention limits may prevent retroactive availability: **do not assume** Bybit can supply any requested past day.

### Consolidated current checklist (source status, not predictive validation)

13 `CALC`: #1 RVOL; #2 Trade Rate; #3 Trade Rate Acceleration; #4 Large Trade Share (as-of 60m P99 first day); #5 Buy/Sell Delta; #6 CVD; #7 Delta Acceleration; #8 Trade Streaks (grouped fill heuristic); #9 Queue Imbalance; #11 Book Depth; #18 Short-Term Autocorrelation; #20 Local Extremes; #29 Spread.

14 `PARTIAL`: #10 Multi-Level OFI (rank-aware first hour, re-indexing); #12 Pulling Rate (visible removal != cancel); #13 Depth Recovery; #14 Price Impact; #15 Effort/Result; #16 Absorption; #17 Exhaustion (exploratory test, inconsistent signs); #19 Range Compression; #21 Sweep/Reclaim; #22 VWAP/Profile (ex-post only); #24 Liquidation Spikes (101 processed records May 19 across five symbols, provider capture completeness unknown); #27 Inter-Trade Duration (fill timestamp duplication); #28 Trade-size Distribution (ex-post P99 + basic sample); #30 Replenishment/Resiliency.

2 `UNVERIFIED FOR MAY 19`: #23 OI Change; #25 Spot / Perpetual Divergence. **Do not interpret missing snapshots as zero**.

1 `DEFERRED LAST`: #26 Cross-Venue Lead-Lag.

**30/30 registered; 13 initial calculations, 14 method/data partial, 2 unknown, 1 deferred; 0 of 30 validated as incremental profitable predictors after fees and OOS.** Earlier base table remains the original historical log; this summary supersedes obsolete checklist status #4/#8/#17 without editing history.

### Joint multivariate study — execution contract, research only

- **Analysis grain:** five symbols × UTC 1-minute as-of states (first day 7200 symbol-minutes), then 5-minute independent anchor subset and multiple days/OOS; STRICT one source observation processed once, no duplicate executions from split fills; distinguish unknown/no-source vs measured zero.
- **Feature blocks:** trade flow (#1-8,#27-28); book liquidity (#9-13,#29-30); market response (#14-22); derivatives/spot (#23-25); #26 later. Store standardized causal scores against each coin's own prior history, plus raw units, sample count, freshness and coverage. No future leakage from full-day P99, POC, VWAP or daily extrema.
- **Labels:** future signed continuation/reversal and realized excursion/returns by 5/15/30/60m, LONG vs SHORT separately. Outcomes must not leak into features. For strategy-level economics evaluate entry/exit costs, slippage and net returns **после комиссий**, not gross direction accuracy.
- **Cross-study first:** interactions among signed Delta/CVD acceleration, rate/RVOL, best/deep OFI, queue imbalance, depth/spread, price efficiency/absorption, then add confirmed OI, liquidations, and Spot. Compare joint model to isolated-feature and price/volatility-only baselines using time-blocked holdout and coin-specific benchmarks, with correlated features grouped to avoid false independence.
- **Dependence guard:** use independent time blocks/episode dedup, walk-forward/OOS, avoid picking thresholds after seeing test labels, stratify by UTC session, trend/volatility/market regime, and include data-quality masks.
- **Output:** one research matrix with columns `symbol, t_utc, feature_version, raw_factors, normalized_asof_factors, coverage_mask, future_labels`; per-symbol × direction interaction report, uncertainty and evidence status. Do not build permanent heavy aggregation architecture until validated source/readers, reuse existing canonical source facts wherever appropriate.


## 2026-10-18 — Bybit OI and Spot for May 19: SOURCE COVERAGE RESOLVED

**CHECKED HERE, not extrapolated from old directories:** Bybit official public OI 5m endpoint successfully paginated for 2026-05-19 UTC: 288/288 timestamps **per each of five coins**, 12 timestamps in every hour, zero missing 5m timestamps, **1440 OI observations total**. Request `category=linear&symbol={SYMBOL}&intervalTime=5min&startTime=1779148800000&endTime=1779235199999&limit=200`, follow `nextPageCursor`. Bybit public Spot daily source `https://public.bybit.com/spot/{SYMBOL}/{SYMBOL}_2026-05-19.csv.gz` was fetched into memory for each coin, parsed successfully with `timestamp` milliseconds. Executions: APT 10074, INJ 24221, DOT 8580, LTC 16860, ARB 10099 (69834 total); **all five have trades in all 24 hours**. Active 1-minute bins: APT 1078, INJ 1302, DOT 1103, LTC 1419, ARB 1151. See exact inventory `research/microstructure/coverage_2026-05-19.md`, commit `5484c99cab5c4f767681522195721e3542e85382`.

Thus earlier statuses #23/#25 as `UNVERIFIED SOURCE` are **superseded as to May19 raw coverage**: now **SOURCE COVERAGE VERIFIED / FACTOR COMPUTATION PENDING**. They are NOT `CALC` until OI delta, Spot/Perp basis and causal data freshness are computed; no OOS or net fee analysis completed. Existing processed liquidation files contain 101 May19 records, but provider capture completeness still unverified. Temporary in-memory retrieval is not durable artifact; next step pin source/payload hashes in a compliant run and implement time-aligned joint multivariate factor matrix, not another list of single-factor anecdotes.


## 2026-10-18 — first JOINT pilot: Bybit OI × Spot basis × signed trade flow

Source published and executed from verified GitHub byte content: `research/microstructure/experiments/bybit_joint_oi_spot_flow_v0.py` commit `efdffecf1d8b735c1b5d41a01776e9cfbdeda55f`. Input: exactly 2026-05-19; Bybit linear public-trade local CSV.GZ, real Bybit public spot archive CSV.GZ and 5m OI official API, **no server files or DB changed**. Preliminary joint feature experiment at 5m anchors: previous-bin signed taker notional direction, OI change at T vs T-5m, previous-bin spot/perp last-price basis, label = following-bin derivative close return. Missing spot bins excluded, not silently filled.

| Coin | Valid aligned rows | Flow direction predicts next 5m | OI increase subset | OI decrease subset | Basis against flow subset |
|---|---:|---:|---:|---:|---:|
| APT | 281 | 133/281 | 56/119 | 77/162 | 65/144 |
| INJ | 286 | 144/286 | 64/136 | 80/150 | 76/151 |
| DOT | 286 | 138/286 | 67/142 | 71/144 | 65/135 |
| LTC | 286 | 132/286 | 42/120 | 90/166 | 67/141 |
| ARB | 285 | 142/285 | 63/131 | 79/154 | 67/133 |

TOTAL 1424 aligned anchors, 689/1424 directional matches = 48.38%; OI-increase subset 292/648 = 45.06%; OI-decrease subset 397/776 = 51.16%. These are **NOT returns after fees; NOT independent replicates; NOT evidence of profitability**. No strategy rule authorized. The naïve OI-sign split is directionally heterogeneous and susceptible to regime/time clustering and reverse causality.

**CRITICAL QC BEFORE USING AS RESEARCH FACTOR:** OI API point labeled T may be released after T (availability timestamp unverified). Although T is used as an as-of point in this exploratory run, this is NOT causal-proof. Shift OI to previously proven available snapshot or validate publication latency in next revision. Same-bin spot last price can be stale; mark age/freshness. Basis using raw last trade may include microstructure noise. Future-label close alignment must be independently reviewed; add market regime baseline, independent episodes and OOS. The v0 report is exploratory ONLY and should not be promoted to CALC causal feature or an edge claim.

## 2026-10-18 — book/flow/liquidation joint pilot
Published code: research/microstructure/experiments/joint_book_flow_liq_v0.py, commit ca6db98d75f7bec86bed091cdcebbdd4b283be8e. Ran on five Bybit symbols for first two hours of 2026-05-19 UTC, read-only. Each symbol had 23 completed-bin anchors. Flow-direction matches next-bin return: APT 13/23, INJ 14/23, DOT 9/23, LTC 6/23, ARB 14/23; total 56/115. Flow and book imbalance agree: 27/64 matches. Disagree: 29/51. Observed-liq subsets: APT 1/2, INJ 5/9, DOT 0/0, LTC 0/2, ARB 0/0. These are descriptive only, not OOS, causal publication-time validated, or results after fees. Critically OI and Spot are NOT integrated with this book pilot on identical rows yet; a unified multi-factor matrix remains PENDING. Cross-venue last.


## 2026-10-18 — OWNER DECISION: 30/30 INDICATOR INVENTORY FIRST; JOINT ANALYTICS FROZEN

**Owner correction supersedes prior joint-model next-step plan.** We do **not** have all 30 indicators ready. All cross-indicator combinations, models, joint matrices, OOS model comparisons and new analytical constructions are **ON HOLD** until the indicator readiness checklist is independently closed. Prior joint pilots remain evidence only. Bybit-only; cross-venue #26 is **last** and must not block completion of the other 29.

**Readiness gate per indicator**: precise physical definition, as-of timestamp/causality, actual source and absence semantics, version-controlled read-only calculator, successful execution on all 5 current coins over same requested test period, output+provenance/QC documented, no known semantic defects. A calculation on one hour or a descriptive ex-post number **does not close** an indicator. OOS profitability/interaction testing begins after this readiness gate, not before it.

**CHECKLIST REALITY NOW: READY 0/30 VERIFIED HERE**. Existing preliminary CALC/PARTIAL values do not meet this stricter full-five/all-period gate. #26 is deferred by owner; count 29 Bybit items + 1 deferred cross-venue. This is a new *readiness* status, not a retraction of earlier descriptive calculations. All 30 have names and prior notes; 30/30 names registered; 0/30 certified research-ready.

| # | Indicator | Existing evidence | Readiness issue |
|---:|---|---|---|
| 1 | RVOL | CALC | Repeatable complete per-minute as-of series + QC |
| 2 | Trade Rate | CALC | Time-series output + coverage/QC |
| 3 | Trade Rate Acceleration | CALC | Explicit intervals and timestamp test |
| 4 | Large Trade Share | CALC preliminary | Causal threshold and versioned series QC |
| 5 | Buy/Sell Delta | CALC | Full causal series, side semantics |
| 6 | CVD | CALC | Cumulative origin and resets documented |
| 7 | Delta Acceleration | CALC | Time-indexed versioned output |
| 8 | Trade Streaks | CALC preliminary | Timestamp-side group heuristic + QC |
| 9 | Queue Imbalance | CALC | As-of sampling and stable depth frame |
| 10 | Multi-Level OFI | PARTIAL | Book rank reindexing, update semantics |
| 11 | Book Depth | CALC | Comparable side and level definitions |
| 12 | Pulling Rate | PARTIAL | Cancel vs execution cannot be inferred from visible decrease |
| 13 | Depth Recovery | PARTIAL | Same-price level matching, elapsed-time causality |
| 14 | Price Impact | PARTIAL | Event horizon, matched execution-price times |
| 15 | Effort / Result | PARTIAL | Causal normalized effort / displacement |
| 16 | Absorption | PARTIAL | Same-price/fill evidence and controls |
| 17 | Exhaustion | PARTIAL | Non-arbitrary physical definition and QC |
| 18 | Short-Term Autocorrelation | CALC | Lag choice and valid price sequence |
| 19 | Range Compression | PARTIAL | Equal-length / causal window normalization |
| 20 | Local Extremes | CALC | Prior-only extrema, no lookahead |
| 21 | Sweep / Reclaim | PARTIAL | Actual intraminute order, not candle-only |
| 22 | VWAP / Volume Profile | PARTIAL | Running as-of values, never day-end peek |
| 23 | OI Change | Source fetched May19 | Validate OI effective/publication timestamp + series |
| 24 | Liquidation Spikes | 101 May19 records | Capture completeness and unknown-vs-zero masks |
| 25 | Spot / Perpetual Divergence | Source fetched May19 | As-of spot freshness and perp synchronization |
| 26 | Cross-Venue Lead-Lag | DEFERRED LAST | Owner: only after Bybit 29 done |
| 27 | Inter-Trade Duration | PARTIAL | Co-timestamp fill handling + as-of series |
| 28 | Trade-size Distribution | PARTIAL | Rolling prior-only quantiles + series |
| 29 | Bid/Ask Spread | CALC | Series/QC and timestamp |
| 30 | Replenishment / Resiliency | PARTIAL | Same-level recovery, true physical event meaning |

The operational next action is **NOT joint analytics**: close each indicator's calculator and evidence in the checklist, with work focused on reusable readers and standard as-of QC only (no cross-factor modeling). Publish each verified gate result to GitHub immediately, no repeated server archaeology. Previous exploratory pilot results are explicitly not considered readiness closure.

 
## 2026-10-18 — first 6 Bybit indicators RESEARCH-READY, NOT OOS-VALIDATED

Implemented `research/microstructure/experiments/trade_minute_factors_v1.py` (commit `f5c6cd03ea756a4eb8b0ab08c0e22d55a91a7644`). Read back exact GitHub source and executed in memory under `cripta` against 2026-05-19 Bybit public trades for all five coins; standard library, stdout only, no server writes. Produces **1440 time-indexed 1-minute rows per coin**, clear minute-closed `available_after_utc`, optional full row JSON, checks count consistency / first 60m warmup; trade no-trade is explicitly count zero; carries close staleness field.

**RESEARCH READINESS GATE PASS for 6/30** (#1 RVOL, #2 Trade Rate, #3 Trade Rate Acceleration, #5 Buy/Sell Delta, #6 CVD, #7 Delta Acceleration); readiness only to provide causally indexed source features for future study, **not** claim of predictive OOS significance or net profitability. The 24 other indicators (including #26 explicitly deferred last) remain open. All six are derived from just Bybit taker-trade records and source timestamps; `CVD` origin = day start UTC and first minute acceleration undefined. Cross-day CVD continuity not promised.

Observed source fill count / active minutes / eligible 60m RVOL minutes / end CVD USD:
- APT 116683 / 1439 / 1380 / +43222
- INJ 368454 / 1440 / 1380 / -4714464
- DOT 143184 / 1438 / 1380 / -224835
- LTC 69495 / 1428 / 1380 / +299290
- ARB 154123 / 1439 / 1380 / -458836

Input source bytes hashed read-only (SHA256):
- APT 4493588 bytes: `da6e6c5b9a2593e0024a95d7207f98c6e7ba836d741dd928dc2e9fae4639c66f`
- INJ 13386017 bytes: `a673d0a44a94c4b8dd284e05e89105f48ea52f03be0c6ef7f42d2a198b7c7eab`
- DOT 5334455 bytes: `0289003c4ba98d3e6dc6a5a009ef8aefcd341a8f142cb3b4701cf768d0491839`
- LTC 2390073 bytes: `d1533d0559bb4efd9ebde77c85200c2aeb35a95ddd8f42e07b85614e5dc11e4b`
- ARB 5837918 bytes: `1f055f174242858ac3d1ab6933385375b44fffac65e683c7c03295bf491c8121`

Caveats: Readiness gate is defined narrowly on verified **one full UTC day per coin**. Multiday windows and OOS still PENDING, planned after 30/30 indicator inventory closes. Future joins may only use a factor after `available_after_utc`; no current-minute peek. No joint analytics performed in this step. 

 
## 2026-10-18 — indicator readiness second batch: 8/30 (two newly passed)

**CHECKED HERE:** Published, read back and executed `research/microstructure/experiments/fill_microstructure_minutes_v1.py`, GitHub fix commit `ab3e54eee1e514b205d7723b6a94ba80611838e5`. Bybit 2026-05-19 public trades, full 1440 minute rows for each of five trading coins, stdout JSON only, no server disk/DB changes. Prior-60-minute P99 fill-notional threshold (never current-day global P99), per-completed-minute fill-notional quantiles. Empty minute is measured zero for large trade share if threshold exists; missing history remains null. Source count reconciles to earlier verified archive counts.

**Additional research-readiness PASS: #4 Large Trade Share, #28 Trade-Size Distribution** (current-minute completed values and rolling causal prior threshold). Ready does NOT mean predictive/OOS validation. Total now **8/30** ready under this pilot-day data-feature gate: #1,#2,#3,#4,#5,#6,#7,#28.

Per coin `fills; minutes with valid large-fill share; minutes with fill quantile P99`:
- APT 116683; 1439; 1439.
- INJ 368454; 1439; 1440.
- DOT 143184; 1439; 1438.
- LTC 69495; 1438; 1428.
- ARB 154123; 1439; 1439.

**#8 Trade Streaks and #27 Inter-Trade Duration still OPEN/PARTIAL.** Earlier grouping by native floating timestamp and new grouping by rounded integer milliseconds are non-equivalent (for APT 29730 vs 28691 groups; INJ 97097 vs 88957; DOT 34659 vs 32851; LTC 14917 vs 14141; ARB 38743 vs 36396). Need verify source timestamp precision and grouping semantics; never infer independent parent orders from fills. Note minor sampling caveat: within-minute P99 is as-of-minute-end descriptive, and requires separate lagging if used at minute start. No joint models until 30-indicator owner gate.


## 2026-10-18 — precision forensic for #8 / #27; execution verification pending

CHECKED HERE via exact Bybit public-trades 2026-05-19 raw CSV.GZ for the five symbols: timestamps are decimal Unix seconds with 4 fractional digits (0.1ms), rather than pure 1ms. Examples include `1779148800.3047` and `1779148800.3052`. Native distinct timestamp count vs incorrectly rounded-ms distinct timestamps: APT 29718 / 28639; INJ 96966 / 88412; DOT 34649 / 32818; LTC 14902 / 14089; ARB 38728 / 36343. Earlier rounded-ms group data were inaccurate for event grouping. A separate `timestamp + side` group count may exceed distinct timestamp count if both sides occur at the same instant; neither represents verified parent orders.

FIX COMMITTED: `research/microstructure/experiments/fill_microstructure_minutes_v1.py` commit `666277d1b799f59476ec4ef2a04b4237caecd05b`, parsing timestamp with `Decimal(seconds)*10000`, 0.1ms integer ticks, minute bucketing 600000 ticks, positive gap converted to milliseconds /10, warning updated. This corrects an identified precision defect; it is **NOT yet independently executed against all five sources in this turn**, since SentinelX execution attempts were tool-security-blocked after initial read-only forensic. Therefore #8 Trade Streaks and #27 Inter-Trade Duration remain **OPEN**, 8/30 ready remains unchanged. No joint analytics or trading behavior changed.

## 2026-10-18 — #8 and #27 readiness QC PASS

CHECKED HERE: exact GitHub source `research/microstructure/experiments/fill_microstructure_minutes_v1.py` was executed under `cripta` for full 2026-05-19 on all five symbols, using exact 0.1ms Decimal timestamps. Raw fill counts / exact-time-and-side groups / minutes with positive intergroup interval feature: APT 116683 / 29730 / 1439; INJ 368454 / 97097 / 1440; DOT 143184 / 34659 / 1438; LTC 69495 / 14917 / 1428; ARB 154123 / 38743 / 1439. Results reproduce prior native-precision grouping counts. Research-readiness gate for #8 Trade Streaks and #27 Inter-Trade Duration PASS at the one-day five-symbol scope. Co-timestamp grouping is heuristic only, not independent parent order IDs; gaps use positive intervals at 0.1ms precision. NO claim of OOS statistical edge. Current READY **10/30**, OPEN 20/30 including #26 cross-venue deferred last. No combinations, deploy, DB or live mutations.

## 2026-10-18 — next price indicator source PREPARED, not verified

Published `research/microstructure/experiments/price_minute_features_v1.py` commit `c242a05fff67dc87a51dd3d4334a3c1808e119e7`. Targets #18 lag-one 60-minute log-return autocorrelation and #20 prior 60-minute close extrema, 1440 rows each, completed-minute availability. Runtime execution was blocked by tool safety at this point, so #18 and #20 remain OPEN / PREPARED, not passed. Reviewed source alone is not test evidence. Readiness stays **10/30**. No joint analytics, other exchanges or LIVE changes.

## 2026-10-18 — #18 and #20 readiness QC PASS

CHECKED HERE: fetched exact GitHub source `research/microstructure/experiments/price_minute_features_v1.py`, ran read-only under actual `cripta` actor against all five complete May-19 public-trade files. Each output covers 1440 one-minute completed as-of records. Counts `coin: trade fills; eligible prior-60 1-minute log-return autocorrelations; close breaks above prior-60-minute close high; close breaks below prior-60 close low`: APT 116683;1380;62;77. INJ 368454;1380;83;104. DOT 143184;1380;64;76. LTC 69495;1380;62;60. ARB 154123;1380;65;79. Passing gates #18 Short-Term Autocorrelation and #20 Local Extremes at one-day five-symbol research-ready scope; per-minute closing state only, no forecasting evidence/OOS. Total **12/30 READY**, remaining 18 open, cross-venue #26 deferred last. No combinations, server writes, DB or LIVE actions.

## 2026-10-18 — book #9/#11/#29 prepared, full-day validation unavailable

CHECKED HERE: GitHub source `research/microstructure/experiments/book_minute_three_v1.py`, committed `2d5ab37939cd1b9e5b178004af4708d6248cb1c4`. Read-only stdlib reconstruction of Bybit ob200 snapshots/deltas; for each completed minute computes queue imbalance ±5bps, bid/ask notional depth ±5bps, bid-ask spread in bps, and age of last book update. Source uses server historical archives and no joins or predictive model. Full-day all-five in-memory invocation returned a SentinelX job handle, then job reported `agent did not respond to exec in 60.0s`; **no reliable results received**. Do NOT infer a successful calculation. Thus #9/#11/#29 remain PREPARED, not READY. Research readiness remains 12/30. No server mutation or LIVE change; next permitted attempt should use bounded single-symbol/hour verification or authorized async compute route and capture verified source-file sizes/hashes. Do not repeat blocked expensive full-day calls.

## 2026-10-18 — #9 #11 #29 FULL-DAY FIVE-SYMBOL QC PASS

CHECKED HERE: current GitHub source `research/microstructure/experiments/book_minute_three_v1.py`, bounded-window update commit `01519ae5010c674bb235c10d2ebab92d244d728c`. The same read-only source was executed as actor `cripta` for 10-minute smoke test on each of five Bybit coins and separately for the FULL UTC day 2026-05-19 on each symbol. Every full-day output yielded 1440/1440 book minute states, snapshots=2 within UTC day; archive contains an additional following-day boundary snapshot counted by earlier file-wide inventory. Full-day messages / median spread bps / median ±5bps queue imbalance / max last-book age ms: APT 606837 / 1.0636 / +0.0235 / 2468; INJ 733303 / 2.0103 / -0.0024 / 917; DOT 631778 / 0.8102 / +0.0166 / 1473; LTC 542094 / 1.8459 / +0.0484 / 1881; ARB 616466 / 0.8656 / -0.0360 / 1088. All five message counts and both medians reproduced existing journal control values at 4 decimals. Factors are book-end-of-minute: #9 Queue Imbalance, #11 Bid/Ask Orderbook Depth in USD within ±5bps of moving mid, #29 Best Bid/Ask Spread in bps. No inference of OFI or cancellations. Valid only strictly AFTER each minute closes, no predictive validation/OOS yet. Readiness gates for #9 #11 #29 **PASS**, total **15/30 READY**, 15 remaining open including cross-venue #26 deferred last. No joined analytics, policy, LIVE or DB changes.

## 2026-10-18 — #19/#22 causal reader: APT smoke PASS, five-symbol QC BLOCKED

- GitHub source: `research/microstructure/experiments/range_vwap_profile_v1.py`, commit `f4d26b2ac7282d412493c37679b0b4f42b5a472a`; exact published source re-read from GitHub. Read-only in-memory execution under Unix actor `cripta`; no server disk or DB writes.
- #19: non-overlapping current/prior windows of 20 completed 1m trade closes. Ratio = (max-min of current 20 closes) / (max-min of preceding 20 closes); null if any missing close or zero prior range. Available only after minute close.
- #22: cumulative same-day per-execution price×size / size VWAP, running exact-price volume histogram, POC = highest accumulated base-asset size with lowest-price tie-break; minute-end publication. Day-end sample is ex-post and is not used earlier.
- APTUSDT, 2026-05-19 UTC: 116683 fills, 1439 active trade minutes, 1440 output rows. #19 valid rows 1361; median compression ratio 0.9833333333333333. #22 end-of-day computed VWAP 0.9401988478587397, POC 0.9385, base volume 7046990.92. At minute 00:39, ratio 1.0, running VWAP 0.9503530765, running POC 0.9482. Execution returned rc=0 in 2.62s.
- QC FINDING: computed APT VWAP 0.9401988479 does not match earlier journal descriptive VWAP 0.940239. Difference not yet explained; before acceptance compare old notional/size source semantics and unit weighting. Exact-price POC matches previous 0.9385.
- Remaining INJ/DOT/LTC/ARB attempts: a multi-invocation tool call was blocked at OpenAI upstream tool-security boundary, before agent execution. Classification `BLOCKED_UPSTREAM_OPENAI`. Do not attribute to SentinelX allowlist or Unix permissions, do not retry in loops or bypass the security gate.
- Result: #19 and #22 still `OPEN` / `PREPARED` with APT-only smoke evidence; no 5/5 QC, no new READY gate. Project remains **15/30 RESEARCH-READY**, joint analytics ON HOLD, cross-venue last, no Strategy/DB/deploy/LIVE changes.

## 2026-10-18 — static source QC finding: #22 prior VWAP definition mismatch

CHECKED HERE by reading both exact sources from current GitHub main: historical `public_trades_day_v1.py` computes `vwap_ex_post=sum(price*foreignNotional)/sum(foreignNotional)`, i.e. quote-notional-weighted mean price, and its POC accumulates `foreignNotional` by price. New `range_vwap_profile_v1.py` computes `running_vwap=sum(price*size)/sum(size)`, i.e. standard base-quantity-weighted VWAP, and accumulates base quantity by exact price for POC. Hence the two VWAP definitions are not equal, and APT 0.940239 old vs 0.9401988479 new is not an arithmetic reproducibility failure of the same estimator. This resolves the *algorithm-definition* discrepancy, but does not replace independent raw `size` / `foreignNotional` consistency QC, source unit validation, or 5/5 runs. APT end-of-day POC matching at 0.9385 is coincidental until equal weighting semantics are established.

No new server execution attempted after prior `BLOCKED_UPSTREAM_OPENAI`. Status remains 15/30 RESEARCH-READY; #19/#22 OPEN with APT smoke only. No policy/runtime/LIVE changes.


## 2026-10-18 — #19/#22 five-symbol full-day execution PASS; independent measurement QC PENDING

CHECKED HERE against GitHub source `research/microstructure/experiments/range_vwap_profile_v1.py` exact commit `f4d26b2ac7282d412493c37679b0b4f42b5a472a`, executed as actual Unix `cripta` under SentinelX for UTC 2026-05-19. APT smoke was previously executed; INJ/DOT/LTC/ARB each now executed separately as bounded read-only single-symbol jobs, returncode 0, no host filesystem/DB writes. Earlier multi-invocation security block was not reissued. Units in this version: per-fill `price` × `size`, VWAP quote/base; POC highest cumulative base quantity per exact traded price; compression ratio two adjacent non-overlapping 20×1m observed close windows, valid only after minute end, null for missing minute/zero preceding range.

| Coin | Fills | Minutes with fills | Output rows | #19 valid windows | #19 median ratio | #22 end VWAP base-quantity weighted | #22 end POC |
|---|---:|---:|---:|---:|---:|---:|---:|
| APTUSDT | 116683 | 1439 | 1440 | 1361 | 0.9833333333 | 0.9401988478587397 | 0.9385 |
| INJUSDT | 368454 | 1440 | 1440 | 1401 | 0.9821428571 | 5.062793211591095 | 4.9100 |
| DOTUSDT | 143184 | 1438 | 1440 | 1358 | 0.9534632035 | 1.236271284039299 | 1.2274 |
| LTCUSDT | 69495 | 1428 | 1440 | 1058 | 1.0000000000 | 54.06578642076478 | 54.12 |
| ARBUSDT | 154123 | 1439 | 1440 | 1361 | 0.9677419355 | 0.1158522179111973 | 0.11661 |

VALIDATION BOUNDARY: 7200 output minute records exist across five instruments, but this script returns sampled rows and summaries; complete 7200-row durable cube artifact has **NOT** been created. Source quantities (`size` units; `foreignNotional` consistency), mathematical independent sample, full input hashes/run manifests, timestamp edge validations, formula-version per-pair passports and output QA remain to be separately attested under new `RESEARCH_COMPUTE §21.6`. 1m no-trade windows explicitly prevent #19 continuity; no filling forward for compression. Ex-post day-end VWAP cannot be used intraday. Prior quote-notional-weighted descriptive VWAP is a **different estimator**, not a direct QC baseline.

Current status: `#19 FULL_DAY_EXECUTED_5_OF_5 / INDEPENDENT_QC_PENDING`; `#22 FULL_DAY_EXECUTED_5_OF_5 / INDEPENDENT_QC_PENDING`. Do NOT count either as newly RESEARCH-READY before per-pair passport/source-QC gate. Historical 15/30 group passes and 75-row audit remain distinct from new §21 attestation. No cross-venue, Strategy/LIVE or joined analysis.


## 2026-10-19 — #19/#22 independent physical QC across five Bybit symbols

CHECKED HERE: independent version-controlled source `research/microstructure/experiments/range_vwap_independent_qc_v1.py`, commit `96543f1f119618cacb657be4230524fcb3c8b2d2`, executed read-only, one symbol/job, on all five Bybit raw trade archives for UTC 2026-05-19 as Unix `cripta`, all rc=0. Independent VWAP accumulator uses CSV `foreignNotional / size`, not the primary reader's `sum(price*size)/sum(size)`. Compression independently rebuilds both adjacent 20-minute close windows. Tolerances: absolute per-fill `foreignNotional - price*size` <= 1e-6 quote units; result comparisons at full printed precision subject to floating/decimal source rounding.

| Instrument | Raw archive SHA256 | Fills | Active minutes | #19 valid | #19 median | #22 independent VWAP | POC base qty | Max `foreignNotional` discrepancy |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| APTUSDT | `da6e6c5b9a2593e0024a95d7207f98c6e7ba836d741dd928dc2e9fae4639c66f` | 116683 | 1439 | 1361 | 0.9833333333333333 | 0.94019884785873968 | 0.9385 | 1E-12 |
| INJUSDT | `a673d0a44a94c4b8dd284e05e89105f48ea52f03be0c6ef7f42d2a198b7c7eab` | 368454 | 1440 | 1401 | 0.9821428571428571 | 5.062793211591095 | 4.9100 | 2E-11 |
| DOTUSDT | `0289003c4ba98d3e6dc6a5a009ef8aefcd341a8f142cb3b4701cf768d0491839` | 143184 | 1438 | 1358 | 0.9534632034632035 | 1.2362712840392992 | 1.2274 | 2E-12 |
| LTCUSDT | `d1533d0559bb4efd9ebde77c85200c2aeb35a95ddd8f42e07b85614e5dc11e4b` | 69495 | 1428 | 1058 | 1 | 54.06578642076478 | 54.12 | 3E-12 |
| ARBUSDT | `1f055f174242858ac3d1ab6933385375b44fffac65e683c7c03295bf491c8121` | 154123 | 1439 | 1361 | 0.967741935483871 | 0.11585221791119729 | 0.11661 | 5E-12 |

All 851939 included fills: 0 quote-notional unit mismatches above 1e-6, 0 unsorted timestamps, 0 outside-day rows in those date-scoped archives, 0 sub-0.1ms-precision violations. 1440 one-minute buckets per symbol. Source schema: timestamp/symbol/side/size/price/tickDirection/trdMatchID/grossValue/homeNotional/foreignNotional/RPI. Input SHA256 reproducibility and independent scalar QC **PASS**; observed no-trade minutes remain absent from #19 valid 40-minute windows.

Per new `RESEARCH_COMPUTE §21`: physical-unit, denominator, as-of-end-of-minute and full-day independent calculation evidence is present for #19 and #22 across 5/5; per-pair status may be marked `PHYSICAL_QC_PASS`. Remaining scope: persist exact separate indicator passports and verify all required §21.6 metadata, output row provenance/aggregator contracts before NEW `RESEARCH-READY` attestation. Historical 15/30 remains historical, with no automatic retroactive promotion of any indicator. No OOS, profitability, Strategy or cross-venue claim.


## 2026-10-19 — per-instrument #19/#22 measurement passports registered, not yet READY

The owner-approved `RESEARCH_COMPUTE §21` now has a machine-readable derived passport at `research/microstructure/passports/range_vwap_2026-05-19_v1.json` (published and corrected exact half-open/inclusive window description by commit `88090916b4ee700e2b6606fd294168671fca7548`). It covers five exact Bybit instruments × two distinct indicators, preserving source SHA256, full-day counts, each coin's scalar independently checked #19 and #22 values, formula/denominator units, previous/current **non-overlapping** close windows [i-39..i-20] and [i-19..i], end-minute timing, missingness and aggregate operators.

10/10 pairs now `PASSPORT_REGISTERED + PHYSICAL_QC_PASS`. The source produces the minute-level 1440 rows internally but existing stdout summary sampled only four per coin; complete output row lineage and separately attested full cube artifacts are still `NOT CHECKED HERE`. **No new per-pair RESEARCH-READY promotion** from merely registering passports. Original 15/30 historical READY remains only the old pilot cohort gate. Next: complete row-level provenance/QC for #19/#22 or continue #10 as independent unfinished candidate; do not pretend that a rank-shifted OFI is strict physical multi-level OFI. No other exchanges, Strategy, release or LIVE modifications.


## 2026-10-19 — #19/#22 per-minute complete-row fingerprints

CHECKED HERE: exact GitHub source `research/microstructure/experiments/range_vwap_profile_v1.py` commit `23aa752c4e32c874ea1d72fb76f9969e96b2b5a5` was executed read-only as actor `cripta`, one symbol per invocation, on each of the five May-19 archives. Source remains no-write; it now hashes every one of 1440 minute records with deterministic sorted-key JSON serialization (including escaped record separator). Existing per-coin fills, medians, day-end base-weighted VWAP and POC match previously independent scalar QC.

| Symbol | Rows hashed | Full-minute-series SHA256 |
|---|---:|---|
| APTUSDT | 1440 | `9a5bdc9060f969f6bfc0008e429b7d4f80c0852ef63c72b88eb1bc154c41a06c` |
| INJUSDT | 1440 | `32eec25aefb7a268d0fa108634fad05cb1622ca69e4c14cbf0c7695c2df31c43` |
| DOTUSDT | 1440 | `93b0c4de532cbbd80c6da77db52cf011c35ffc6f3f0e95f685f760a896f4b193` |
| LTCUSDT | 1440 | `54052a27f736f41f05e8dbbd39628512a6d8b72e8d0c9da6f76e49e3ac529252` |
| ARBUSDT | 1440 | `3f5727195e3d5f8bcb3c81c1cc9f170846c90293ae5f80ddc8f63c18b4ba39ba` |

Derived passport `research/microstructure/passports/range_vwap_2026-05-19_v1.json` binds the above hashes and exact reader source commit per instrument. NOTE: hash evidence is not a separately persisted full row dataset. Remaining: verify independent full-row causal temporal alignment and output artifact/provenance; `NEW_RESEARCH_READY` not promoted. Historical 15/30 pilot READY stays historical.


## 2026-10-19 — #10 strict-price-identity book flow 5×10min smoke

New bounded, read-only script `research/microstructure/experiments/price_key_book_flow_v1.py`, commit `b9bbef84f73a09b46fe9de174cb04346ff5bd6e9`, run as `cripta` for 10 minutes of 2026-05-19 on all five symbols (five rc=0). It updates Bybit ob200 snapshots/deltas, tracks **exact price identity** between previous/new top-five sets and sums changes in quote USDT **only for levels appearing in both top-five sets**. This avoids rank-reindexing false quantity deltas but excludes entering/leaving top-five prices; therefore the metric is **visible fixed-price common-level quantity change**, not full strict multi-level OFI, and is neither Pulling Rate nor fills-vs-cancels classification.

| Symbol | First-10-min delta transitions | Bid fixed-price change USDT | Ask fixed-price change USDT | Signed bid-minus-ask USDT |
|---|---:|---:|---:|---:|
| APTUSDT | 4825 | -74739.32 | -27956.60 | -46782.72 |
| INJUSDT | 5561 | +599152.70 | +383812.64 | +215340.06 |
| DOTUSDT | 4861 | -22937.21 | +8541.90 | -31479.11 |
| LTCUSDT | 4377 | -115294.18 | -187887.38 | +72593.21 |
| ARBUSDT | 4547 | +69191.71 | +97220.47 | -28028.76 |

These are **descriptive signed book-liquidity changes**, not price direction predictions; numbers are symbol-specific, no cross-coin averaging. Five smoke checks passed. Full-day APT run launched as bounded isolated SentinelX job `job_b9bd7306e2a6`: at the time of launch job continued in background; outcome **NOT VERIFIED** until notification/result; do NOT mark complete from process existence. #10 stays OPEN; no strategy, deployment or LIVE changes.


## 2026-10-19 — #10 APT full-day exact-price visible book-flow execution

CHECKED HERE: SentinelX job `job_b9bd7306e2a6` completed `status=succeeded`, exit code 0, no truncated output, duration 77.43 s. Single-symbol full-day 2026-05-19 UTC APTUSDT `price_key_book_flow_v1.py` source commit `b9bbef84f73a09b46fe9de174cb04346ff5bd6e9`; snapshot resets=2, delta transitions=606835 (source book messages previously recorded 606837 including two snapshots), updated minute buckets 1440. Exact-price common-top5 visible quote-notional changes: bid -5170483.52 USDT, ask -468018.80 USDT, signed bid-minus-ask -4702464.73 USDT. This measures changes in visible book quantities, NOT net taker flow, cannot distinguish fills/cancellations, excludes top-five membership transitions and has not demonstrated strict comprehensive multi-level OFI. Symbol APT pilot-day full pass is a **RESEARCH RESULT**, indicator #10 remains OPEN pending physical definition/coverage/5-coin full day QC. Full-day run was automatically backgrounded under same job; result was retrieved and verified, not rerun. No host writes, trading rules or LIVE.


## 2026-10-19 — #10 INJ full-day same-price-key book flow

CHECKED HERE: completed SentinelX job `job_f719f7404126` (exit 0, 101.27s, no truncation) running `price_key_book_flow_v1.py` exact GitHub commit `b9bbef84f73a09b46fe9de174cb04346ff5bd6e9` under `cripta`, Bybit INJUSDT UTC 2026-05-19. 2 book snapshot resets, 733301 delta events, all 1440 minutes with updates. Visible same-price common-top5 change in quote notional: BID +14680789.86 USDT, ASK +29414204.71 USDT, signed difference -14733414.85 USDT. Same APT full-day method previously passed 606835 deltas. **This is NOT strict complete multi-level OFI**, since disappearing/entering top-five price identities are excluded; no fill/cancel attribution or future price label; indicator #10 OPEN. Distinct per-coin history retained; no cross-coin averaging, strategy/LIVE changes.

## 2026-10-19 — #10 DOT full-day fixed-price flow

CHECKED HERE: SentinelX job `job_bee76398049c` succeeded rc=0, no truncated output, duration 87.94s, same immutable `price_key_book_flow_v1.py` commit `b9bbef84f73a09b46fe9de174cb04346ff5bd6e9` and 2026-05-19 UTC Bybit DOTUSDT. Two snapshots, 631776 delta transitions, 1440 minutes with updates; common exact-price top-five flow Bid -3605825.06 USDT / Ask -5402589.38 USDT, signed +1796764.32 USDT. NOT full multi-level OFI; rank membership entry/exit excluded and no cancellation/fill attribution. #10 OPEN. Research only, no cross-coin averages, no LIVE.

## 2026-10-19 — #10 LTC full-day fixed-price flow

CHECKED HERE: SentinelX single bounded read-only exec succeeded rc=0, duration 41.88s, exact `price_key_book_flow_v1.py` commit `b9bbef84f73a09b46fe9de174cb04346ff5bd6e9`, Bybit LTCUSDT 2026-05-19 UTC. Two snapshots, 542092 delta transitions, 1440 minutes with updates; same-price common-top5 Bid -9288709.74 USDT / Ask -12962898.43 USDT, signed +3674188.69 USDT. Not full strict OFI (top-five membership changes excluded). #10 remains OPEN.


## 2026-10-19 — #10 price-identity common-top5 book flow: 5/5 full-day executions

CHECKED HERE: immutable research-only `research/microstructure/experiments/price_key_book_flow_v1.py` commit `b9bbef84f73a09b46fe9de174cb04346ff5bd6e9`, separate `cripta` actor runs for Bybit 2026-05-19, 1440 minutes each, all exit zero, each with two snapshots. Five complete per-coin observed outcomes, independently preserved with no cross-symbol averaging:

| Symbol | Delta transitions | Bid change USD | Ask change USD | Bid minus Ask USD |
|---|---:|---:|---:|---:|
| APTUSDT | 606835 | -5170483.52 | -468018.80 | -4702464.73 |
| INJUSDT | 733301 | +14680789.86 | +29414204.71 | -14733414.85 |
| DOTUSDT | 631776 | -3605825.06 | -5402589.38 | +1796764.32 |
| LTCUSDT | 542092 | -9288709.74 | -12962898.43 | +3674188.69 |
| ARBUSDT | 616464 | +4663839.36 | +6081975.47 | -1418136.11 |

Last ARBUSDT job `job_7b045cb3beef` succeeded exit=0 after 86.44s, output not truncated. Previous APT `job_b9bd7306e2a6`, INJ `job_f719f7404126`, DOT `job_bee76398049c` succeeded; LTC completed synchronously exit=0 in 41.88s. Book event totals reconcile to prior message inventory minus two in-day snapshots per symbol; minute coverage 1440/1440.

**Physical meaning:** sum of signed price×quantity changes solely at exact price keys present in both previous and current top five on their own side. Entering/exiting the top five is excluded, so this is not complete strict multi-level OFI and must not be so named in the Analytical Cube. It cannot separate quote cancellation from executed depletion. Large absolute sums reflect repeated book updates, not traded money, price movement, PnL, or strategy edge. #10 remains OPEN pending comprehensive strict OFI method and verification. This stage **does close full-day five-coin coverage for the narrower fixed-price book-change metric**.

No second venue, Strategy, DB, runtime deployment, or LIVE mutation.


## 2026-10-19 — #10 top-five membership decomposition 5×10-minute smoke

CHECKED HERE: read-only `research/microstructure/experiments/book_top5_membership_v1.py`, GitHub commit `de93b95502244cc62c7fed9429de2a61c95fdd24`, ran under `cripta`, all five symbols, first 10 minutes of May 19 2026 UTC, return code 0. The source reconstructs ob200, explicitly decomposes changes at common exact price keys and **entry/exit of top-five membership**. It further distinguishes membership shifts that had price keys already present in the broader book from newly appearing/disappearing price keys. These are **stock changes**, not standard OFI and not cancellation/filled quantity attribution.

| Symbol | Delta events | Common-price bid change USDT | Bid top-five net stock change USDT | Common-price ask change USDT | Ask top-five net stock change USDT |
|---|---:|---:|---:|---:|---:|
| APTUSDT | 4825 | -74739.32 | +2651.71 | -27956.60 | +1585.76 |
| INJUSDT | 5561 | +599152.70 | -1314.91 | +383812.64 | +66.96 |
| DOTUSDT | 4861 | -22937.21 | +4693.56 | +8541.90 | +2131.38 |
| LTCUSDT | 4377 | -115294.18 | +2187.22 | -187887.38 | +2466.78 |
| ARBUSDT | 4547 | +69191.71 | -48.97 | +97220.47 | +1118.48 |

Physical FINDING: using only common-price changes can grossly misrepresent **change of total top-five visible stock** because of rank-boundary entry/exit. Net top-five stock movement is near zero on some instruments despite large opposing component changes. These are distinct valid measurements with distinct definitions, **not interchangeable trading signals**. Causal data are descriptive after event update, timestamps/latency and full-day/independent QC remain to verify. #10 OPEN until a properly defined multi-level OFI is implemented and verified. No other venues, trading mutation or LIVE.


## 2026-10-19 — #10 top-five decomposition APT full-day verified

CHECKED HERE: same immutable `book_top5_membership_v1.py` commit `de93b95502244cc62c7fed9429de2a61c95fdd24` executed actor `cripta` for full UTC 2026-05-19 APTUSDT, SentinelX job `job_8f20b4a23ebb` succeeded exit=0 in 80.54s, output not truncated. 2 snapshots, 606835 delta events, 1440 updated minute buckets.

- BID: common exact-price changes **-5170483.52 USDT**, top-five entering-price stocks **+49111003.15**, leaving-price stocks **-43934713.49**, resulting net top-five visible-stock change **+5806.13**.
- ASK: common exact-price changes **-468018.80 USDT**, top-five entering-price stocks **+47223143.50**, leaving-price stocks **-46749875.00**, resulting net **+5249.70**.
- Of BID entering-price stock, +36573239.04 was already elsewhere in the broader reconstructed book before entry into top-five; ASK +34499443.68 similarly. Thus membership changes are not directly new quote placement, cancellation or executions.

This is a physical **component decomposition** of top-five visible stock and not strict multi-level OFI, predictive direction, or net traded flow. Next independent source coverage and complete five-coin runs remain open. No Strategy/LIVE changes.

## 2026-10-19 — #10 INJ complete 24h top-five membership decomposition

CHECKED HERE: job `job_0f20795619d1`, exit 0, 104.72s; `book_top5_membership_v1.py` immutable commit `de93b95502244cc62c7fed9429de2a61c95fdd24` executed read-only under `cripta` for INJUSDT 2026-05-19 UTC. Book deltas 733301, snapshots 2, updated minutes 1440/1440. BID common-level +14680789.86 USDT, entering top-five stock +220997286.06, leaving -235672430.95; net top-five BID stock +5644.96. ASK common-level +29414204.71, entering +247420034.46, leaving -276819714.04; net ASK +14525.13. Of entering BID/ASK stock, +183341586.04/+206774619.10 already existed outside top-five. These entry and exit effects MUST NOT be labeled executed trades or pure cancellations. #10 still OPEN (stock decomposition != strict OFI), remaining DOT/LTC/ARB full-day for this variant pending; no Strategy/LIVE.

## 2026-10-19 — #10 DOT full-day top-five membership decomposition

CHECKED HERE: job `job_cdcadb8bc9e1` succeeded, rc=0, 92.07s, no truncated output. Versioned `book_top5_membership_v1.py` commit `de93b95502244cc62c7fed9429de2a61c95fdd24`; `cripta` actor read-only, DOTUSDT 2026-05-19 UTC; 631776 deltas, 2 snapshots, updated 1440/1440 minutes. BID common-price -3605825.06 USDT, entry +89343130.74, exit -85732274.93, total top-five stock +5030.76. ASK common-price -5402589.38, entry +87451908.76, exit -82056742.55, top-five stock -7423.17. Inside previously broader book: entered BID 61036285.77, ASK 58154317.53 USDT; these are reclassification, not direct order creation. #10 strict multi-level OFI OPEN; this is bounded descriptive membership decomposition, not fill/cancel or PnL.

## 2026-10-19 — #10 LTC full-day top-five membership decomposition

CHECKED HERE: actor `cripta` read-only script `book_top5_membership_v1.py` commit `de93b95502244cc62c7fed9429de2a61c95fdd24`, LTCUSDT 2026-05-19 UTC, completed rc=0 in 44.53s, two snapshots, 542092 deltas, 1440 minutes. BID common -9288709.74 USDT, entry +102974252.68, exit -93635617.09, net top-five stock +49925.85. ASK common -12962898.43, entry +102409340.71, exit -89464220.29, stock -17778.01. Entry from already present deeper levels: BID +75623378.10 USDT, ASK +79222245.16. This is a top5 stock decomposition; strict OFI, cancel/fill attribution, OOS and predictive conclusions remain OPEN.


## 2026-10-19 — #10 five-symbol 24h top-five membership decomposition complete

CHECKED HERE: research-only `book_top5_membership_v1.py` immutable commit `de93b95502244cc62c7fed9429de2a61c95fdd24` executed separately as `cripta` on the complete UTC day 2026-05-19 for all five symbols, each returned rc=0. Each has 2 snapshot resets and 1440/1440 minute buckets with updates. New verified jobs: INJ `job_0f20795619d1`, DOT `job_cdcadb8bc9e1`, LTC synchronous rc=0, ARB `job_62731347cfe1`. Earlier APT `job_8f20b4a23ebb`.

| Symbol | Delta events | Common bid USDT | Entered bid stock USDT | Exited bid stock USDT | Top5 bid net stock USDT | Common ask USDT | Top5 ask net stock USDT |
|---|---:|---:|---:|---:|---:|---:|---:|
| APTUSDT | 606835 | -5170483.52 | +49111003.15 | -43934713.49 | +5806.13 | -468018.80 | +5249.70 |
| INJUSDT | 733301 | +14680789.86 | +220997286.06 | -235672430.95 | +5644.96 | +29414204.71 | +14525.13 |
| DOTUSDT | 631776 | -3605825.06 | +89343130.74 | -85732274.93 | +5030.76 | -5402589.38 | -7423.17 |
| LTCUSDT | 542092 | -9288709.74 | +102974252.68 | -93635617.09 | +49925.85 | -12962898.43 | -17778.01 |
| ARBUSDT | 616464 | +4663839.36 | +64575468.50 | -69241160.96 | -1853.11 | +6081975.47 | -6044.35 |

ASK entering/exiting values, respectively: APT +47223143.50/-46749875.00; INJ +247420034.46/-276819714.04; DOT +87451908.76/-82056742.55; LTC +102409340.71/-89464220.29; ARB +71742337.37/-77830357.19. All components belong to **that exact coin**, not shared denominators. Enormous gross transitions with far smaller net visible top-five stock changes show reclassification/rank-boundary effects, not equal actual trades. This is a fully executed **book stock decomposition**, NOT validated strict multi-level OFI, NOT cancellation/fill attribution, and NOT OOS/predictive profitability. #10 remains OPEN with narrower variant FULL_DAY_5_OF_5. No DB, deployment or LIVE change.


## 2026-10-19 — strict best-price transition OFI: 5-coin 10m pilot

CHECKED HERE: new read-only algorithm `research/microstructure/experiments/top1_price_transition_ofi_v1.py` published at GitHub commit `f5485b7cb729195e8ef6ea394d31f9ae94acf444`, executed under actor `cripta`, Bybit orderbook first ten minutes 2026-05-19 UTC for all five instruments, separately and successfully rc=0. For each delta, exact best-price bid effect is +new bid quantity if bid price rises, quantity difference if unchanged, -old quantity if bid price falls. Ask effect is +new ask quantity if ask price falls, difference if unchanged, -old ask quantity if ask price rises. OFI = bid effect minus ask effect in each **instrument's base units**, with snapshot resetting previous state.

| Coin | Paired transitions | Bid contribution (base) | Ask contribution (base) | Top1 OFI (base) |
|---|---:|---:|---:|---:|
| APTUSDT | 4824 | +90041.74 | +120062.94 | -30021.20 |
| INJUSDT | 5560 | +3574.9 | -521.6 | +4096.5 |
| DOTUSDT | 4860 | +156953.0 | +194314.2 | -37361.2 |
| LTCUSDT | 4376 | +3674.8 | +2987.0 | +687.8 |
| ARBUSDT | 4546 | +331587.6 | +223614.1 | +107973.5 |

This is a legitimate **best-price transition OFI prototype only**. It is NOT proven strict multi-level OFI, NOT a buy/sell aggressor measurement, not classified cancel-vs-fill, not a prediction or OOS. Absolute base quantities cannot be compared across coins. Snapshot boundary contributes no pairwise delta. First 10-minute source coverage is 5/5; full-day validation and independent QC still pending. Indicator #10 remains OPEN; no Strategy/LIVE mutation.


## 2026-10-19 — #10 Top1 price-transition OFI, full-day 5/5 CHECKED HERE

Immutable source `research/microstructure/experiments/top1_price_transition_ofi_v1.py` commit `f5485b7cb729195e8ef6ea394d31f9ae94acf444`; separate read-only execution under Unix `cripta` for each of five Bybit symbols, complete UTC 2026-05-19, five exit codes 0. Two snapshots reset reference, one prior-state-less delta after each snapshot excluded from paired transitions. This computes price-conditional best-bid/ask OFI, NOT full multi-level OFI.

| Symbol | Paired deltas | Bid component (base) | Ask component (base) | Top1 OFI bid-minus-ask (base units) |
|---|---:|---:|---:|---:|
| APTUSDT | 606833 | 7975632.48 | 7271140.10 | +704492.38 |
| INJUSDT | 733299 | 353182.1 | 546674.4 | -193492.3 |
| DOTUSDT | 631774 | 12192153.8 | 11585271.6 | +606882.2 |
| LTCUSDT | 542090 | 310152.9 | 266852.8 | +43300.1 |
| ARBUSDT | 616462 | 24466568.1 | 25842902.3 | -1376334.2 |

These quantities are BASE ASSET quantities specific to each instrument, not USDT; no coin-averaging, not inferred cancellations/execution, not returns or profitability. Status for this narrow indicator: `TOP1_FULL_DAY_5_OF_5_EXECUTED`; independent formula QC, per-minute lineage and §21 registration still to be checked. **#10 strict multi-level OFI remains OPEN** until its complete methodology and verification are finished. No trading deployment or LIVE changes.


## 2026-10-19 — #12 visible same-price depletion 5-coin 10m smoke

CHECKED HERE: source `research/microstructure/experiments/same_price_depletion_v1.py` commit `fa52681ecc380fa8ca74f678b40f67d25eb58137`, five separate read-only server runs actor `cripta`, 2026-05-19 00:00–00:10 UTC, five rc=0. For already present exact orderbook prices, sums all **positive gross visible reductions** `price×(old_qty-new_qty)` and separately gross increases of same-price quantities; both quote-USDT per coin; no aggregation across coins.

| Symbol | Book deltas | Bid gross visible reduction USDT | Ask gross visible reduction USDT | Bid same-price gross increase USDT | Ask same-price gross increase USDT |
|---|---:|---:|---:|---:|---:|
| APTUSDT | 4825 | 16867943.08 | 15615408.15 | 16183518.76 | 14706392.56 |
| INJUSDT | 5561 | 96059928.65 | 93221996.54 | 94453472.08 | 92472721.63 |
| DOTUSDT | 4861 | 36694272.68 | 38217841.41 | 33260970.77 | 34469015.56 |
| LTCUSDT | 4377 | 21167331.71 | 19216312.71 | 21104504.68 | 19373482.32 |
| ARBUSDT | 4547 | 14377294.74 | 13464815.34 | 13851893.86 | 12155745.50 |

**Important limitation**: shrinking visible level quantity can be a cancellation, a filled passive order, or exchange book aggregation and cannot be positively labeled `Pulling Rate` without validated trade/book causal attribution. Gross flows re-count liquidity churn, not P&L or unique money; new level additions are excluded. Status: **#12 PHYSICAL_DEPLETION_CANDIDATE_SMOKE_5_OF_5**, strict `Pulling Rate` remains OPEN. Next bounded investigation is reconciliation of observed trade fills vs per-price book deltas with timestamp availability; preserve uncertainty rather than fabricate cancellations. Source is stdout-only, no DB, deploy, Strategy or LIVE.


## 2026-10-19 — #13 same-exact-price depth recovery episodes five-coin 10m smoke

CHECKED HERE: read-only `research/microstructure/experiments/exact_price_recovery_v1.py` commit `7b24dc2a89a936a8b9a5cbc643f17f13e2f5a200`, five 10-minute single-coin Bybit 2026-05-19 UTC runs under actor `cripta`, 5/5 exit 0. After an observed size decrease at a known exact price, one episode is followed for at most 60s, ending RECOVERED if visible size again reaches at least its **pre-decrease** quantity, EXPIRED if window elapsed, or OPEN if right-censored by sample end/snapshot. Repeated decreases while an episode is pending are not double-counted.

| Coin | Bid episodes | Bid recovered/expired/open | Bid median recovered ms | Ask episodes | Ask recovered/expired/open | Ask median recovered ms |
|---|---:|---|---:|---:|---|---:|
| APTUSDT | 2033 | 1299/668/66 | 6700 | 1570 | 941/592/37 | 6900 |
| INJUSDT | 3368 | 2258/963/147 | 4300 | 2765 | 1837/823/105 | 4500 |
| DOTUSDT | 2058 | 1279/698/81 | 4800 | 1788 | 1113/612/63 | 5900 |
| LTCUSDT | 1112 | 670/393/49 | 8102 | 824 | 495/299/30 | 7000 |
| ARBUSDT | 2059 | 1277/712/70 | 6800 | 1647 | 1001/586/60 | 8199 |

All ten bid/ask `events = recovered + expired + open` episode accounting checks PASS. This is only a **same-price visible-size recovery** proxy; levels can reprice/recycle and source does not prove identity of liquidity provider, causal provider availability, predictive usefulness or 60-second coverage completeness. Censored OPEN episodes MUST NOT be treated as failure. Status: #13 `RECOVERY_EPISODE_CANDIDATE_SMOKE_5_OF_5`; full-day, independent QC and passport remain OPEN. No Strategy/LIVE changes.


## 2026-10-19 — #14 minute-level signed flow vs future price-response pilot, 5/5 full UTC day

CHECKED HERE: immutable research-only `research/microstructure/experiments/signed_flow_next_minute_v1.py` commit `abdd8f6939c2e356b5641adffdd09536efa314ac`, five successful separate actor-`cripta` computations on public Bybit fills for 2026-05-19 1440 minute buckets per instrument. Physical source field `foreignNotional` (quote USDT) signed by aggressor side to construct **current completed-minute** delta; future *label* is next minute's close-to-close return from last transaction of minute i to minute i+1, multiplied by sign of minute-i delta. A causal feature is available at close i, while the future label is only available after close i+1. This is NOT same-event microstructural price impact; future leakage forbidden.

| Symbol | Valid pairs / 1439 | Favorable | Adverse | Flat | NO_DATA | Median directional next-minute return bps |
|---|---:|---:|---:|---:|---:|---:|
| APTUSDT | 1437 | 693 | 650 | 94 | 2 | 0 |
| INJUSDT | 1439 | 639 | 721 | 79 | 0 | -1.88288458 |
| DOTUSDT | 1435 | 660 | 662 | 113 | 4 | 0 |
| LTCUSDT | 1415 | 550 | 566 | 299 | 24 | 0 |
| ARBUSDT | 1437 | 684 | 676 | 77 | 2 | 0 |

These are within-one-day **exploratory direction-conditioned labels** only. In particular the INJ negative median does not establish an edge; no baseline/control, OOS, causal physical trade-impact attribution, strategy threshold, or after-fee PnL has been tested. #14 remains OPEN as a strict Price Impact indicator. Separately retained per-coin outcome counts, no across-symbol pooling. No exchange mutation, DB, runtime or LIVE change.


## 2026-10-19 — #15 explicit 1m effort/result physical proxy, five full days

CHECKED HERE: read-only `research/microstructure/experiments/effort_result_minute_v1.py` at GitHub commit `0fb8d91b442c3096247013e0cbe85b89e848ef25`, actor `cripta`, five successful full-day UTC 2026-05-19 executions. Definition: `abs(last_trade_price / first_trade_price - 1) × 10000 bps / (sum(foreignNotional) / 1,000,000 USDT)` per **completed one-minute** Bybit trade bucket. This is a named **endpoint-move-per-turnover physical proxy** in bps per million USDT, not a marginal execution-impact estimator, not true high-low range and not cross-coin-normalized liquidity state.

| Coin | Valid of 1440 minutes | No-trade minutes | Median bps per 1m USDT | P90 bps per 1m USDT |
|---|---:|---:|---:|---:|
| APTUSDT | 1439 | 1 | 2024.08999 | 11877.34759 |
| INJUSDT | 1440 | 0 | 481.59572 | 2006.35740 |
| DOTUSDT | 1438 | 2 | 630.71074 | 3323.15659 |
| LTCUSDT | 1428 | 12 | 458.56195 | 3076.42518 |
| ARBUSDT | 1439 | 1 | 880.82341 | 4297.04163 |

Zero-turnover denominators encountered: 0 among nonempty minutes. NO_DATA on empty minute, NOT 0. The large difference per coin reflects this specific ratio's sensitivity to turnover, tick size and liquidity; **not evidence** that one coin is a better trading opportunity. Unit/denominator known; cross-period normalizers, microstructure controls, independent computation QC and OOS are pending. #15 `FULL_DAY_PHYSICAL_PROXY_5_OF_5 / RESEARCH_READY_PENDING`, no Strategy/DB/LIVE changes.


## 2026-10-19 — #13 exact-price depth recovery completed 5/5 full UTC day

CHECKED HERE: versioned `research/microstructure/experiments/exact_price_recovery_v1.py` commit `7b24dc2a89a936a8b9a5cbc643f17f13e2f5a200`; read-only under actor `cripta`, UTC 2026-05-19, 5 independent rc=0 completed-day runs. Each has two snapshots, all 1440 minute buckets included. For each symbol, observed same-price visible-size reductions are tracked at most once per still-pending price; outcome RECOVERED within 60s, EXPIRED after 60s, or right-censored OPEN at day/snapshot boundary. Accounting `events = recovered + expired + open` PASS on all ten coin/side groups.

| Symbol | Bid events | Bid recovered | Bid expired | Bid open | Bid median recovery ms | Ask events | Ask recovered | Ask expired | Ask open | Ask median recovery ms |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| APTUSDT | 229804 | 143019 | 86596 | 189 | 7200 | 175813 | 104438 | 71242 | 133 | 6099 |
| INJUSDT | 330990 | 208355 | 122457 | 178 | 6300 | 275054 | 168838 | 106110 | 106 | 5700 |
| DOTUSDT | 244016 | 151261 | 92578 | 177 | 5700 | 214615 | 132968 | 81516 | 131 | 4900 |
| LTCUSDT | 135020 | 79756 | 55158 | 106 | 7101 | 102288 | 59686 | 42526 | 76 | 4899 |
| ARBUSDT | 241467 | 148240 | 93064 | 163 | 6700 | 198615 | 121185 | 77311 | 119 | 6000 |

**Causality/limit:** this is a same-price **visible-size reappearance proxy**, not verified same order/provider recovery, market resiliency edge, or proof that depletion came from cancellations. A price may disappear and later reappear; episode logic does not prove continuous queue identity. Median times are for recovered events only (selection conditional), not unconditional hazard/survival. Complete-day executed coverage = 5/5; independent QC and a formal per-pair §21 passport remain pending. **#13 FULL_DAY_PHYSICAL_PROXY_5_OF_5, RESEARCH-READY NOT PROMOTED**. No trading policy, DB, deployment or LIVE mutations.


## 2026-10-19 — #16 high-flow / price-resistance candidate, completed day 5/5

CHECKED HERE: read-only `research/microstructure/experiments/absorption_prior60_candidate_v1.py`, GitHub commit `3ffd9594f0aa950cfaf4ec438c36c6d4888bb636`, actor `cripta`, all five Bybit public-trades sources 2026-05-19 full UTC day; five successful rc=0. Current **completed** one-minute signed aggressor `foreignNotional` (USDT) qualifies as unusually high when absolute signed flow exceeds per-instrument p90 of **prior 60 nonempty minutes only**. Within that same already-completed minute, record signed price response (last-minus-first trade price), and separately count aligned, opposite, unchanged. Current-minute information is not known until minute close. This is a *resisted-flow candidate*, **not proven physical absorption** by passive order flow.

| Coin | Eligible minute buckets (after initial 60) | Unusually high flow | Price against flow | Price with flow | No price change | NO_DATA | Opposite move after buy/sell dominant flow |
|---|---:|---:|---:|---:|---:|---:|---|
| APTUSDT | 1379 | 151 | 29 | 117 | 5 | 1 | 18/11 |
| INJUSDT | 1380 | 154 | 16 | 133 | 5 | 0 | 8/8 |
| DOTUSDT | 1378 | 159 | 24 | 128 | 7 | 2 | 14/10 |
| LTCUSDT | 1368 | 163 | 20 | 129 | 14 | 12 | 14/6 |
| ARBUSDT | 1379 | 151 | 27 | 122 | 2 | 1 | 14/13 |

QC five of five: every unusually-high-flow count = aligned+against+flat. No cross-coin normalization, no future lookahead in baseline; per-coin results differ. **Critical**: high signed flow and weak/opposite minute endpoint change does not identify passive absorbers, true hidden liquidity, traded order origins, microstructure economics, OOS advantage or fee-adjusted returns. This version has no matched controls. #16 `FULL_DAY_CAUSAL_PROXY_5_OF_5 / PHYSICAL_ABSORPTION_NOT_PROVEN`; RESEARCH-READY not promoted. No trading, service, DB or LIVE mutations.


## 2026-10-19 — #17 completed 5-minute flow decay and next-block reaction: 5/5 day

CHECKED HERE: source `research/microstructure/experiments/flow_decay_5m_v1.py` GitHub commit `5e17415ac7ffaf5c9056bba7833a3fa2845176ae`; five read-only `cripta` runs, 5 rc=0 on Bybit trades UTC 2026-05-19. Each day has 288 nonoverlapping five-minute blocks. For study anchor i, previous completed price movement (block i-2 last close to block i-1 last close) agrees with last two signed flow blocks; latest/previous absolute signed USDT flow ratio is reported, with `<1` meaning physical decrease, NOT a Strategy threshold. **Future** block i+1 close is a separate outcome label only available at its close. Study anchors step every five minutes and have overlapping history/label exposures; do NOT treat them as independent OOS samples.

| Coin | Eligible anchors | Median latest/prior absolute flow | Decreasing-flow events | Next reversal/continuation/flat after decay | Nondecreasing events | Next reversal/continuation/flat otherwise |
|---|---:|---:|---:|---|---:|---|
| APTUSDT | 103 | 0.9951 | 52 | 22/27/3 | 51 | 27/24/0 |
| INJUSDT | 101 | 1.3106 | 46 | 23/21/2 | 55 | 24/31/0 |
| DOTUSDT | 101 | 1.0298 | 49 | 27/22/0 | 52 | 25/26/1 |
| LTCUSDT | 101 | 0.6978 | 57 | 29/26/2 | 44 | 27/15/2 |
| ARBUSDT | 104 | 0.6795 | 59 | 28/31/0 | 45 | 23/22/0 |

Five QC partition checks passed. Exploratory per-coin outcomes are mixed; **no universal exhaustion prediction** established, and no controls, independent samples, OOS, fee-adjusted P&L, or physical order attribution. #17 `FULL_DAY_FLOW_DECAY_FUTURE_LABELS_5_OF_5`, strict Exhaustion `RESEARCH-READY` remains OPEN. No trading, deploy, or LIVE mutation.


## 2026-10-19 — #21 trade-tape price sweep and ordered reclaim 5/5 full-day pilot

CHECKED HERE: read-only `research/microstructure/experiments/sweep_reclaim_ordered_trades_v1.py` commit `b0b5d2c05d488ed34ff0d5f0c6b5cfe7b5d13c63`, five successful actor-`cripta` Bybit 2026-05-19 full-day executions. For minute i, reference high/low is the highest/lowest **transaction price** in **exactly previous 60 fully populated minute buckets** (strictly before i), no filling of empty buckets. Within i ordered trade prints must first pass >high or <low and then print back <=high or >=low **later** to count a reclaim. Same-minute reclaim is a sequence, not merely a high/low summary.

| Coin | Eligible minutes | Missing/incomplete windows | High sweeps | High sweep→reclaim | Low sweeps | Low sweep→reclaim |
|---|---:|---:|---:|---:|---:|---:|
| APTUSDT | 1319 | 61 | 51 | 30 | 56 | 34 |
| INJUSDT | 1380 | 0 | 54 | 41 | 86 | 73 |
| DOTUSDT | 1316 | 64 | 49 | 28 | 59 | 29 |
| LTCUSDT | 958 | 422 | 30 | 15 | 41 | 21 |
| ARBUSDT | 1319 | 61 | 43 | 24 | 65 | 37 |

All 5/5 QC `reclaims <= sweeps` true. LTC valid coverage much lower because exact complete preceding 60-minute trade windows prohibit silent gap filling. Values are distinct from old journal prior60-close-extrema or intra-minute-OHLC prototypes, **not their regression replacement**: source estimator and inclusion rules differ. Trade-print sequence does NOT establish a physical orderbook liquidity sweep or unique ordering of identical timestamp fills; those need additional source/source-sequence QC. #21 `ORDERED_TRADE_TAPE_PROXY_FULL_DAY_5_OF_5`, strict Sweep/Reclaim OPEN. No Strategy/LIVE changes.


## 2026-10-19 — #23 historical Open Interest bounded inventory, five coins

CHECKED HERE via `research/microstructure/experiments/oi_coverage_probe_v1.py`, exact GitHub commit `d57cc29ebb631fe96f3f1ed714327bb4e4c42ed1`, read-only `cripta` actor, five return codes 0. For 2026-05-19, no symbol-specific OI/open_interest-named child was found by shallow inspection of the known raw dataset symbol directories or obvious external provider family names. Per APT, INJ, DOT, LTC, ARB: `HISTORICAL_OI_NOT_ESTABLISHED`, `exact_day_valid_oi_series_verified=false`, 0 shallow candidate matches. **This is NOT exhaustive filesystem search and does NOT prove historical OI is nonexistent** in all server databases, vendor layouts, or older archives; it does prove no verified OI dataset was recovered by this bounded path probe.

#23 remains `OPEN / SOURCE_COVERAGE_NOT_VERIFIED`; absent evidence is `NO_DATA`, not zero OI or a zero-change signal. No trading rule, DB, service or LIVE changes. Next is #24 liquidation coverage (source provenance and hourly holes).


## 2026-10-19 — #24 liquidation files: five-coin hourly coverage explicitly incomplete

CHECKED HERE: read-only `research/microstructure/experiments/liquidation_hour_coverage_v1.py` GitHub commit `89ba8fe2eb2a13e62dfe8f260e1cd89b88d375d3`, five successful `cripta` actor executions, target CryptoHFTData Bybit path `/data/cripta/datasets/external/cryptohftdata/bybit_liquidations_v1/raw/2026-05-19/{HH}/{SYMBOL}_liquidations.parquet.zst`.

| Coin | Hours with nonempty files / 24 | Present hours UTC | Sum of compressed bytes | Missing hours |
|---|---:|---|---:|---:|
| APTUSDT | 7 | 00,09,11,14,16,17,18 | 10697 | 17 |
| INJUSDT | 15 | 00,01,02,03,04,05,09,10,11,14,16,17,18,19,22 | 24831 | 9 |
| DOTUSDT | 1 | 14 | 1637 | 23 |
| LTCUSDT | 2 | 00,14 | 3162 | 22 |
| ARBUSDT | 4 | 14,16,22,23 | 6197 | 20 |

Physical file presence and byte length are **not proof** that ALL true exchange liquidation events were captured even in present hours; file absence can reflect no events OR missing capture, unresolved until provider's completeness semantics established. Entire missing hours must be `NO_DATA` for unbiased event-rate claims, never zero counts. Thus #24 has `SOURCE_FILES_PRESENT_PARTIAL_HOURLY_COVERAGE`, `Liquidation Spikes` predictive indicator still OPEN, no 5/5 full-day event denominator acceptance, no trade/LIVE change.


## 2026-10-19 — #25 bounded historical Spot/Perpetual source inspection

CHECKED HERE: read-only `research/microstructure/experiments/spot_perp_source_probe_v1.py` GitHub commit `7bb42e8e4e3dac9470b7bf34b596090b82baccdf`, five successful actor-`cripta` probes for 2026-05-19 APTUSDT, INJUSDT, DOTUSDT, LTCUSDT, ARBUSDT. Inspected only obvious spot-named sibling directories under `/data/cripta/datasets/raw/20260518_20260816`, spot-named children under each symbol and top-level spot-named external providers; **no candidate path found** and no valid causal Spot series verified. This is a bounded physical-path scan, not exhaustive proof of nonexistence in other files, vendor layouts or PostgreSQL.

#25 remains `OPEN / SPOT_SOURCE_UNVERIFIED`, cannot compute actual Spot/Perp divergence or assign zero spread from perpetual-only data. Missing historical Spot observations are `NO_DATA`. No cross-venue investigation was started; #26 remains explicitly deferred last. No trading, database or LIVE change.


## 2026-10-19 — #30 top-five total-depth shock/recovery, five-symbol 10m smoke

CHECKED HERE: source `research/microstructure/experiments/top5_depth_resilience_v1.py` GitHub commit `014f4f162b113d36a03398863a19ac385319263b`, five separate read-only `cripta` executions, rc=0, first 10 minutes of 2026-05-19 UTC. Distinct from #13 exact-price recovery: here a **total top-five visible-depth** fall per Bid/Ask side starts one pending event, and restoration of same side's cumulative `sum(price×quantity)` in quote USDT to its pre-fall stock within 60s marks RECOVERED. Otherwise EXPIRED or right-censored. Snapshot causes reset. One pending event per side by design; no queue ownership/fill attribution.

| Coin | Bid episodes | Bid recovered/expired/censored | Bid median recovered ms | Ask episodes | Ask recovered/expired/censored | Ask median recovered ms |
|---|---:|---|---:|---:|---|---:|
| APTUSDT | 40 | 34/5/1 | 2300 | 43 | 35/7/1 | 800 |
| INJUSDT | 44 | 39/4/1 | 4100 | 46 | 40/5/1 | 2001 |
| DOTUSDT | 38 | 32/5/1 | 1399 | 41 | 35/5/1 | 4100 |
| LTCUSDT | 46 | 40/5/1 | 2800 | 59 | 54/4/1 | 1299 |
| ARBUSDT | 43 | 37/5/1 | 901 | 48 | 43/4/1 | 2101 |

Episode accounting `episodes=recovered+expired+censored` PASS all ten side-series. Caveats: repricing/re-ranking can move top-five totals without actual same-order restoration, repeated falls within an open episode are not independent, and fast recovery rates here do not prove exchange resilience or any profitable feature. Status #30: `TOP5_STOCK_RECOVERY_10M_SMOKE_5_OF_5`, full-day independent QC, causality/latency and strict replenishment remain OPEN. No trading/DB/deploy/LIVE changes.


## 2026-10-19 — #30 APT full-day top-five stock resilience result

CHECKED HERE: source `research/microstructure/experiments/top5_depth_resilience_v1.py` commit `014f4f162b113d36a03398863a19ac385319263b`, read-only actor `cripta`, 2026-05-19 UTC APTUSDT, rc=0, duration 42.73s, two snapshots, 606835 book deltas. BID: 5281 top-five-depth fall episodes, 4468 recovered within 60s, 811 expired, 2 right-censored, median time **among recovered** 1800ms. ASK: 5416 episodes, 4623 recovered, 791 expired, 2 censored, median recovered 1500ms. Both episode partitions PASS. The same top-five quote-depth proxy remains vulnerable to re-ranking, and neither identical liquidity provider nor cancel/fill causation is established. Other four full-day runs NOT CHECKED HERE at this checkpoint. #30 full-day coverage 1/5.


## 2026-10-19 — #30 INJ full-day top-five stock resilience result

CHECKED HERE: `top5_depth_resilience_v1.py` GitHub commit `014f4f162b113d36a03398863a19ac385319263b`, actor `cripta`, UTC 2026-05-19 INJUSDT, SentinelX job `job_7dcc675b32b0` succeeded exit 0, duration 56.23s, no truncated output. 2 snapshots, 733301 delta transitions. BID: 5184 episodes, 4395 recovered within 60s, 787 expired, 2 right-censored; median recovery among recovered 2400ms. ASK: 5430 episodes, 4639 recovered, 789 expired, 2 censored; median 2000ms. Both episode partitions PASS. Strict physical same-order resiliency and attribution remain OPEN; current result is top-five depth-stock proxy. #30 full-day coverage now 2/5, other symbols DOT/LTC/ARB not yet counted.


## 2026-10-19 — #30 DOT full-day top-five stock resilience result

CHECKED HERE: `research/microstructure/experiments/top5_depth_resilience_v1.py` exact commit `014f4f162b113d36a03398863a19ac385319263b`, actor `cripta`, UTC 2026-05-19 DOTUSDT, SentinelX job `job_e398ecf07535` succeeded exit 0, 47.5s, no truncation. 2 snapshots, 631776 book deltas. BID: 5615 episodes, 4815 recovered, 798 expired, 2 right-censored; median 1500ms among recovered. ASK: 5661 episodes, 4865 recovered, 794 expired, 2 right-censored; median 1400ms. Both episode partitions PASS. #30 top-five-stock full-day verified 3/5; LTC/ARB still pending here. Physical liquidity owner, repricing and true order-level restoration remain unverified; not strict replenishment.


## 2026-10-19 — #30 LTC full-day top-five stock resilience result

CHECKED HERE: `top5_depth_resilience_v1.py` GitHub commit `014f4f162b113d36a03398863a19ac385319263b`, actor `cripta`, UTC 2026-05-19 LTCUSDT, successful synchronous execution exit=0, duration 22.23s. Two snapshots and 542092 deltas. BID: 6343 top-five depth-fall episodes; 5547 recovered, 795 expired, 1 censored; median 1100ms among recovered. ASK: 6779 episodes; 6016 recovered, 761 expired, 2 censored; median 1000ms. Both episode partitions PASS. #30 top-five-depth-stock proxy full-day coverage 4/5; ARB still pending. Does NOT demonstrate refill of same order/queue or trading edge.


## 2026-10-19 — #30 top-five quote-depth stock resilience, full-day 5/5 checkpoint

CHECKED HERE: `research/microstructure/experiments/top5_depth_resilience_v1.py` exact version commit `014f4f162b113d36a03398863a19ac385319263b`; five separately executed read-only jobs under actor `cripta`, complete 2026-05-19 UTC Bybit order-book archives. Each includes two snapshot resets, all in-day delta transitions, and 1440 minute buckets in scope. All five exit codes 0. Last ARB `job_b325280cb123` succeeded duration 45.92s, no truncation.

A **top-five total quote-depth** side reduction starts a single pending episode; recovery means aggregate same-side top-five quote depth at or above pre-reduction target within 60 seconds, with EXPIRED versus censored OPEN distinguished. `events = recovered + expired + censored` PASS for all ten per-coin/side sequences.

| Coin | Bid episodes | Bid recovered | Bid expired | Bid censored | Bid median recovered ms | Ask episodes | Ask recovered | Ask expired | Ask censored | Ask median recovered ms |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| APTUSDT | 5281 | 4468 | 811 | 2 | 1800 | 5416 | 4623 | 791 | 2 | 1500 |
| INJUSDT | 5184 | 4395 | 787 | 2 | 2400 | 5430 | 4639 | 789 | 2 | 2000 |
| DOTUSDT | 5615 | 4815 | 798 | 2 | 1500 | 5661 | 4865 | 794 | 2 | 1400 |
| LTCUSDT | 6343 | 5547 | 795 | 1 | 1100 | 6779 | 6016 | 761 | 2 | 1000 |
| ARBUSDT | 5116 | 4327 | 788 | 1 | 2001 | 5087 | 4279 | 806 | 2 | 2001 |

This closes 5/5 **full-day computations of the explicitly scoped top-five quote-depth stock recovery proxy**, and is **NOT** proof of same-price replenishment, same liquidity owner, true event-driven shock causation, predictive efficacy, an order-level resiliency indicator, or economic advantage. Repricing, re-ranking and churn can restore aggregate depth without restoring any original order. Reference #13 separately tracks exact price-level recovery and is NOT interchangeable. Independent replay/QC and §21 per-pair passports still pending. #30 strict Replenishment/Resiliency remains OPEN; no promotion to RESEARCH-READY, no trading or LIVE changes.


## 2026-10-19 — STOP checkpoint: #24 extended parquet row-level reader access denied by SentinelX command policy

CHECKED HERE: while attempting to inspect optional Parquet support for source-content QC on #24 historical liquidation files, system `python3` under `cripta` was verified to have **no** importable `pyarrow`, `pandas`, `duckdb`. A read-only existence smoke confirmed `/data/cripta/research/cache/test_gate_venv/bin/python` exists, but **NOT** that it has the requested packages.

Attempt to invoke that alternate interpreter via SentinelX `sentinel_exec` under `sudo -u cripta` with `-B -c` inline package introspection received:
```text
ok=false
error=command_not_allowed
message="contains shell operator ';'. the operator ';' is part of a shell pipeline or redirection..."
```
Per `DEVELOPMENT_RELEASE §19.5–19.6`, this is **SentinelX policy/parser refusal**, NOT OpenAI upstream tool-security, NOT Unix EACCES, NOT a host Python package failure and NOT proof that the command reached the host. The proposed command used a semicolon inside quoted Python code, which the SentinelX parser rejected before execution. The **default allowlisted system Python** has already completed many successful read-only research tasks; this refusal concerns the *alternate-interpreter probe only*.

Decision in this run: NO retries/re-encoding/actor changes/allowlist changes, no installation, no server mutation. `#24_FULL_EVENT_CONTENT_QC` remains `NOT_CHECKED_HERE`; prior per-coin file presence/hour manifest remains verified evidence, but liquidations event completeness unknown. `HARD_STOP=YES` for the blocked alternate interpreter probe under current method. `OWNER_DECISION_REQUIRED=YES` **only if** an exception/new tool command policy or approved interpreter route is needed; this is not a request to grant privileges or modify agent configuration without evidence. Trading services, DB and LIVE unchanged. Latest all-30 checkpoint registry is `research/microstructure/INDICATOR_PROGRESS_2026-10-19.md`.


## 2026-10-19 — #12 same-price visible book-depletion complete day, five instruments

CHECKED HERE: independent bounded server executions (five rc=0) of immutable GitHub `research/microstructure/experiments/same_price_depletion_v1.py`, source commit `fa52681ecc380fa8ca74f678b40f67d25eb58137`, actual actor `cripta`, complete Bybit UTC 2026-05-19 ob200 event histories. All five symbols, 2 snapshots each, same full-day delta totals reconciled to prior #10/#30 inventories. Observed per-exact-price existing-level gross reduction and increase use `price*(old_qty-new_qty)` / `price*(new_qty-old_qty)` in USDT; newly appearing price levels are excluded from the increase statistic. These are gross repeated changes, NOT unique traded notional, cancellation-only pull, or net flow.

| Symbol | Delta events | Bid gross depletion USDT | Bid gross increase USDT | Ask gross depletion USDT | Ask gross increase USDT |
|---|---:|---:|---:|---:|---:|
| APTUSDT | 606835 | 1701661974.315956 | 1637183919.560335 | 1447067552.556300 | 1391739813.827868 |
| INJUSDT | 733301 | 7951935026.27380 | 7849636871.75960 | 7611646408.93350 | 7499672805.22730 |
| DOTUSDT | 631776 | 4131251277.88466 | 3831085106.72139 | 4127829259.44416 | 3756269821.75760 |
| LTCUSDT | 542092 | 2014476802.914 | 1976796144.875 | 1775664181.997 | 1737975267.169 |
| ARBUSDT | 616464 | 1663437867.415008 | 1554128642.627728 | 1451256210.057091 | 1347944067.073256 |

Causality boundary: the visible decrement at a book price is observed after a book delta; such decrements can reflect passive fills, cancellation, aggregation or unknown effects. There is NO safe direct conversion of these sums to `Pulling Rate`. All five full-day **narrow physical measurements** complete; separate event-level trade/book alignment, measurement passport, independent replay and attribution remain OPEN. `#12_VISIBLE_DEPLETION_FULL_DAY_5_OF_5` but strict #12 Pulling Rate `RESEARCH-READY=NO`. No trading/DB/runtime/LIVE mutations. Server alternate-interpreter denial recorded earlier does not affect these approved standard Python executions.


## 2026-10-19 — #12 event-time book-depletion ↔ public-trade proximity, 5×10m smoke

CHECKED HERE: new read-only `research/microstructure/experiments/depletion_trade_temporal_join_v1.py` published, corrected **numeric Decimal exact-price equality** commit `1c0c6beaca1e3959af2650cdf334a39c03462a2a`; executed as actor `cripta` on Bybit public trade tape + ob200 book, UTC 2026-05-19 00:00–00:10 for five symbols separately, five rc=0. Exact price and passive book side: buyer-initiated fill tests Ask, seller-initiated fill tests Bid. Book reduction event counted as `old_qty > new_qty` at any price in a book delta. A symmetric **±1000ms exploratory time window** asks if at least one trade occurred at matching price/passive side, without allocations and without causal ordering. This ±1000ms condition explicitly looks **after** book event and is NOT a permitted as-of input or live trading signal. Books reconstructed from snapshot/delta; each instrument had one snapshot, 10-minute book windows completed.

| Symbol | Trades | Book deltas | Price-level reduction events | With trade ±1s same price/side | Without matching trade ±1s |
|---|---:|---:|---:|---:|---:|
| APTUSDT | 1277 | 4825 | 17350 | 858 | 16492 |
| INJUSDT | 5549 | 5561 | 33614 | 2872 | 30742 |
| DOTUSDT | 1474 | 4861 | 19793 | 934 | 18859 |
| LTCUSDT | 448 | 4377 | 9538 | 246 | 9292 |
| ARBUSDT | 1568 | 4547 | 16837 | 765 | 16072 |

5/5 partition QC: reduction events = matched + unmatched. **Not cancellation attribution**: unmatched can be unmatched fills, cross-feed latency, missing events or changes not known; matching can be coincidental, count one trade multiple times, and price/time ordering may be ambiguous. The numerical match shares should NOT be read as cancellation shares or execution share. This is an explicit research diagnostic for improving source availability and alignment, not `RESEARCH-READY` for #12 Pulling Rate. No additional Python interpreter used; earlier denied interpreter route not retried. No LIVE/DB/deploy changes.


## 2026-10-19 — #12 full-day five-instrument book/depletion vs trade-proximity diagnostic

CHECKED HERE: immutable `research/microstructure/experiments/depletion_trade_temporal_join_v1.py` source commit `1c0c6beaca1e3959af2650cdf334a39c03462a2a`, standard allowlisted `sudo -u cripta python3`, five rc=0, full 2026-05-19 UTC archives. Joined each **positive-previous-quantity price-level reduction** in ob200 against same exact Decimal price, passive book side and any opposite aggressor public trade within ±1000ms. Result is an **exploratory association with hindsight**, NOT causal attribution; post-event trades are included. A single trade can match multiple reductions. Exact source counts match previously attested five-coin book delta and trade totals, plus independent earlier `same_price_depletion_v1.py` side decrease-event counts; all five `reductions=matched+unmatched` QC PASS.

| Symbol | Trade prints | Book deltas | Price-level reductions | With nearby trade ±1s | Without nearby trade ±1s |
|---|---:|---:|---:|---:|---:|
| APTUSDT | 116683 | 606835 | 1883472 | 61235 | 1822237 |
| INJUSDT | 368454 | 733301 | 3058923 | 170014 | 2888909 |
| DOTUSDT | 143184 | 631776 | 2264922 | 76262 | 2188660 |
| LTCUSDT | 69495 | 542092 | 1049895 | 27128 | 1022767 |
| ARBUSDT | 154123 | 616464 | 2133614 | 78656 | 2054958 |

Absence of a nearby trade is NOT proof of cancellation: separate publication latencies, delayed fills, unrepresented executions, book semantics and exchange chronology are unresolved. A nearby trade is also NOT proof the trade caused the observed book reduction. Do not convert these counts into cancellation rate, inferred pulling, profitability or Strategy. These full-day joins are evidence for **coverage and feed-alignment hypothesis**, not #12 Pulling Rate `RESEARCH-READY`. Neither rejected alternative interpreter nor access policy bypass was used. Trading/LIVE unchanged.


## 2026-10-19 — #12 timing sensitivity 5×10m, individual coin diagnostic

CHECKED HERE: new read-only `research/microstructure/experiments/depletion_trade_timing_sensitivity_v1.py`, immutable source commit `9736ac82c18cc512036ad5a1d76b3dadc5a85943`, actor `cripta`, first 10 minutes 2026-05-19 UTC five Bybit instruments, 5/5 exit=0. All five reduction counts reconcile to previous exact-price trade/book join. Match definition: any exact-price+passive-side public trade within 0 / 100 / 500 / 1000 / 5000 milliseconds of orderbook quantity decrease, using reported source timestamps. Separately count (a) symmetric ±window, (b) trade timestamp no later than book message, (c) trade timestamp later than book message. Past/future columns overlap when trades exist on both sides; not additive. **None is validated observation/publication time.**

| Symbol | Reduction events | Symmetric 0ms | Symmetric ±100ms | Symmetric ±500ms | Symmetric ±1000ms | Symmetric ±5000ms | Past timestamp within 1000ms | Future timestamp within 1000ms |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| APTUSDT | 17350 | 3 | 469 | 668 | 858 | 1944 | 463 | 463 |
| INJUSDT | 33614 | 10 | 1384 | 2175 | 2872 | 5777 | 1795 | 1490 |
| DOTUSDT | 19793 | 0 | 430 | 664 | 934 | 2138 | 413 | 556 |
| LTCUSDT | 9538 | 0 | 108 | 171 | 246 | 660 | 129 | 124 |
| ARBUSDT | 16837 | 0 | 436 | 617 | 765 | 1774 | 445 | 340 |

QC: 5/5 monotone symmetric window counts PASS. The source can produce overlaps and reuses trade prints; these counts **MUST NOT** be interpreted as share of fills or cancellation rate. A +100ms/+5000ms *future* print relative to book event is a retrospective association and is never a permissible as-of Strategy feature. Exposure to latent exchange-feed publication lag remains open. Do not infer pulling from unmatched reductions. #12 is still OPEN; daily timing comparison next, no LIVE.


## 2026-10-19 — #12 five-coin full-day trade/book timing sensitivity completed

CHECKED HERE: pinned source `research/microstructure/experiments/depletion_trade_timing_sensitivity_v1.py` commit `9736ac82c18cc512036ad5a1d76b3dadc5a85943`, actor `cripta`, five separate rc=0 full-day runs on Bybit 2026-05-19 UTC. Baseline `depletion_trade_temporal_join_v1.py` matched exactly at ±1000ms for all five coins, and reduction counts reconciled against previous #12 physical depletion runs. Each symbol includes 2 book snapshots. Count of price-level reductions with at least one reported same-price/passive-side trade in selected symmetric windows:

| Coin | Reductions | ±0ms | ±100ms | ±500ms | ±1000ms | ±5000ms | Past within 1000ms | Future within 1000ms |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| APTUSDT | 1883472 | 127 | 33411 | 47460 | 61235 | 142313 | 34855 | 29056 |
| INJUSDT | 3058923 | 444 | 88356 | 130838 | 170014 | 355953 | 104851 | 85749 |
| DOTUSDT | 2264922 | 129 | 39083 | 58990 | 76262 | 177173 | 38410 | 41286 |
| LTCUSDT | 1049895 | 32 | 13637 | 20632 | 27128 | 63330 | 17243 | 12669 |
| ARBUSDT | 2133614 | 140 | 45686 | 62221 | 78656 | 182819 | 46822 | 35528 |

CHECKED HERE: symmetric matches monotone with widening time windows for all five; ±1000ms exactly equal to prior five-coin full-day `depletion_trade_temporal_join_v1` outputs. Past-only and future-only can overlap; their sum is NOT necessarily symmetric because different trades can appear on either side of one book update. Reported source event time is not verified publication/observed time and orderbook decreases are not proven cancellations. Future window is hindsight, never an as-of feature. A single trade may match many book updates; quantity allocation and unique causality unverified. **#12 remains OPEN, indicator Pulling Rate not physically identified.** Remaining research: source timing/availability calibration, event-sequence identity, true cancellation-vs-fill identification; no Strategy or LIVE changes.


## 2026-10-19 — #12 timestamp QC pilot and bounded timeout checkpoint

CHECKED HERE: source `research/microstructure/experiments/trade_book_timestamp_qc_v1.py` published commit `130db7d3e1a6d362a17db4605edcc6a38de8fe54`. Exact 10-minute read-only Bybit public trades and ob200 inventories, actor `cripta`:

| Coin | Trade rows | Trade adjacent same-ms timestamps | Trade backward | Book messages | Book adjacent same-ms | Book backward | Result |
|---|---:|---:|---:|---:|---:|---:|---|
| APTUSDT | 1277 | 886 | 0 | 4826 | 0 | 0 | PASS |
| INJUSDT | 5549 | 4250 | 0 | 5562 | 0 | 0 | PASS |
| DOTUSDT | 1474 | 1069 | 0 | 4862 | 0 | 0 | PASS |
| LTCUSDT | NOT VERIFIED | — | — | NOT VERIFIED | — | — | TIMEOUT |
| ARBUSDT | NOT CHECKED | — | — | NOT CHECKED | — | — | NOT RUN |

These 3 verified book feeds expose top-level `cts,ts,type,data,topic` and book data `a,b,s,seq,u`; public trade fields include `trdMatchID` and `timestamp`. These fields are useful for a later explicit physical timing/sequencing study, **but mere presence does not prove that cross-feed arrival/order semantics align**. The book sequence has zero adjacent equal millisecond ts in this 10m sample, unlike trades. Multiplicity of same millisecond trade timestamps creates ambiguity for naïve trade↔book joins. No cancellation-vs-fill attribution.

For LTC same command hit SentinelX bounded `duration=30.87s, returncode=-1, timed_out=true`, reported process killed. Per `DEVELOPMENT_RELEASE §19.6` timeout is **NOT** Unix EACCES or confirmed successful research. Did not immediately retry same compute, did not label LTC PASS, and did not invoke a parallel actor. ARB was not run after timeout. `#12_SOURCE_TIMESTAMP_QC` = PARTIAL 3/5. Original prior full-day #12 results remain verified independently. No LIVE/Strategy/DB mutation.

## 2026-10-19 — #12 ARB timestamp QC 10m independently recovered

CHECKED HERE: pinned source `research/microstructure/experiments/trade_book_timestamp_qc_v1.py` commit `130db7d3e1a6d362a17db4605edcc6a38de8fe54`, actor `cripta`, bounded 10-min read-only Bybit 2026-05-19 ARBUSDT, rc=0, 1.3s. 1568 trade rows, 1215 adjacent equal millisecond timestamps, zero backwards; 4548 ob200 messages including one snapshot, 0 adjacent equal millisecond and 0 backwards. Source keys: `cts,ts,type,topic,data`, `data.seq,u,s,a,b`. Cross-feed global ordering, publication latency and cancellation attribution remain unverified. Previous LTC timeout remains unresolved; not inferred pass.


## 2026-10-19 — #12 bounded first-500 book/trade clock sample all five

CHECKED HERE: read-only `research/microstructure/experiments/book_trade_clock_sample_v1.py` code commit `81cd4a91e72b6afb610680d0b0778c837d60e033`, key label clarified in `ce57f18694bc011aa2e1cbdc1bbe0ccf57ec8dad`. Five independent actor-`cripta` executions rc=0, capped first 500 book messages and up to 500 first-10-minute trade records for Bybit 2026-05-19. This replaces NO result from earlier LTC full 10m timeout with a **different, bounded sampling method**; it does NOT retrospectively mark the timed-out full-scanning code successful.

| Instrument | Sampled trade rows | Sampled book messages | `ts - cts` min..max (ms) | `seq` nondecreasing | `u` nondecreasing |
|---|---:|---:|---|---|---|
| APTUSDT | 500 | 500 | 1..98 | yes | yes |
| INJUSDT | 500 | 500 | 1..100 | yes | yes |
| DOTUSDT | 500 | 500 | 1..100 | yes | yes |
| LTCUSDT | 448 | 500 | 1..101 | yes | yes |
| ARBUSDT | 500 | 500 | 1..112 | yes | yes |

The numeric expression is **`ts - cts`** (not reversed). The output field at first execution was mistakenly labeled `cts_minus_ts_ms_minmax`; the value computation was correct and code label immediately fixed in subsequent GitHub commit. Book data had `seq,u` and top-level `cts,ts`. Nondecreasing sequence in a 500-message slice does NOT prove no missing updates over the day; publication clock, feed order and trade/book causality are still unverified. A trade after book event in source timestamp remains unavailable to any causal as-of feature. Strict Pulling Rate #12 stays OPEN; no trading/runtime/DB/LIVE changes.


## 2026-10-19 — #19 Range Compression / #22 VWAP-Volume Profile: independent second day, 5/5 source coverage

CHECKED HERE: read-only `research/microstructure/experiments/range_vwap_profile_v1.py`, exact source version resolved from main commit `ae8c45517dbee1056f36714db7809153c59d9b27`, executed as effective actor `cripta` for five Bybit instrument trade files on **2026-05-20 UTC**, separate successful rc=0 each. This is **a second calendar day**, not the prior control-day 2026-05-19 reused. All 5 result rows contain deterministic SHA256 for the 1440 serialized minute records. Full 20m/previous20m range compression requires 40 successive traded-minute closes; missing minutes stay null and cannot be counted as zero. Running VWAP/POC are computed with data available up to each completed minute; end-day summaries are **ex-post diagnostics**, not historical earlier-as-of features.

| Symbol | Trade prints | Minutes with trades /1440 | Valid range-compression minutes | Median range ratio | End-day running VWAP (quote) | End-day exact-price POC (quote) | SHA256 all 1440 minute rows |
|---|---:|---:|---:|---:|---:|---:|---|
| APTUSDT | 117069 | 1440 | 1401 | 0.96875 | 0.9445962380876828726150498456 | 0.9491 | db43bdc14a4054bd03d1252010a96b8f7ce7be6db9128631231c8977d806c0de |
| INJUSDT | 260994 | 1440 | 1401 | 1.035087719298245614035087719 | 4.997061870279427281673446231 | 4.8080 | 34e751c86f85f47773eb39fe7b0faaacacc7892120948b914b97ed2480ea59f3 |
| DOTUSDT | 151067 | 1436 | 1277 | 1.033333333333333333333333333 | 1.241365570440215917996754777 | 1.2544 | 226e018fae1b43b11c7d7c230db868b59acbcaebc8e9564cd205ffdb5f0efac6 |
| LTCUSDT | 93645 | 1423 | 1010 | 0.923076923076923076923076923 | 53.96085386015687462062675412 | 54.19 | 33f3bacef04cfb3dd38407cb4940c8b7c2555057929ad05cf7635b03c1e56797 |
| ARBUSDT | 209152 | 1440 | 1401 | 0.972222222222222222222222222 | 0.1124200047801860609436016683 | 0.11330 | fa8e685c1960e5fb6b85280b40ce0f27fb8908ee3da821b343d21b969f3a2c9c |

FINDING: missing trades in DOT/LTC reduce eligible contiguous 40-minute windows considerably; correctly distinct from ZERO compression. This is a **second-day 5/5 calculator execution with row hashes**, not an independent implementation agreement on 2026-05-20, not a no-leak end-to-end system proof, and not OOS trading validation or after-fee PnL. #19/#22 per-instrument second-day source-and-calculation coverage now checked; independent reader second-day QC and §21 passport update remain OPEN. #26 cross-venue still last. No Strategy/Execution/DB/deploy/LIVE mutations.

## 2026-10-19 — #19/#22 independent reader path mismatch FINDING

CHECKED HERE: `research/microstructure/passports/range_vwap_2026-05-19_v1.json` names `research/microstructure/experiments/range_vwap_independent_qc_v1.py` as independent reader with historical commit `96543f1f119618cacb657be4230524fcb3c8b2d2`. A GitHub `fetch_file(ref=main)` for **that exact path** returned `NOT_FOUND (404)` during second-day independent-QC preflight. This is a **current-main path/documentation mismatch finding**, not evidence that the independent reader never existed at historical commit, nor authority to invent a replacement or silently rewrite the passport. Independent 2026-05-20 agreement is **NOT CHECKED HERE**; previous primary reader 5/5 results and hashes remain valid narrow evidence. Before RESEARCH-READY promotion, locate the actual GitHub main implementation and reconcile current passport pointer through approved documentation process. No Strategy/LIVE changes.


## 2026-10-19 — #19/#22 historical independent reader recovered; second-day independent QC PASS 5/5

CHECKED HERE: GitHub `main` tree recursive inspection confirmed **`range_vwap_independent_qc_v1.py` absent on main**, while historical repository tree at commit `96543f1f119618cacb657be4230524fcb3c8b2d2` contained the file named in the original 2026-05-19 passport. Historical contents fetched and inspected; its `date` was hardcoded to 2026-05-19. A **new separately versioned** `research/microstructure/experiments/range_vwap_independent_qc_v2.py` source was published in `main` commit `fa683e2a27e50a1a18360c5c894d1ab2352bdf4a`; only parameterized exact UTC date and CLI input while preserving independent book-free trade CSV loop/derived calculations. The historical v1 passport pointer was NOT silently modified or claimed resolved for current main; this is a documented new research variant.

CHECKED HERE: five independent read-only `cripta` runs of v2 on **2026-05-20 UTC** returncode=0, same inputs as primary `range_vwap_profile_v1.py` second-day 5/5 execution recorded previously.

| Coin | Independent fills | Active minutes | Independent range valid | Independent median range ratio | Independent POC | Feed order inversions | foreignNotional consistency errors |
|---|---:|---:|---:|---:|---|---:|---:|
| APTUSDT | 117069 | 1440 | 1401 | 0.96875 | 0.9491 | 0 | 0 |
| INJUSDT | 260994 | 1440 | 1401 | 1.035087719298245614035087719 | 4.8080 | 0 | 0 |
| DOTUSDT | 151067 | 1436 | 1277 | 1.033333333333333333333333333 | 1.2544 | 0 | 0 |
| LTCUSDT | 93645 | 1423 | 1010 | 0.923076923076923076923076923 | 54.19 | 0 | 0 |
| ARBUSDT | 209152 | 1440 | 1401 | 0.972222222222222222222222222 | 0.11330 | 0 | 0 |

Primary vs independently derived valid counts, medians, active minutes, fills and POC all **EXACT MATCH (5/5)**. Independent end-day VWAP sourced from CSV `foreignNotional`, primary VWAP recomputed from `price * size`; tiny Decimal differences occur (APT 0.9445962380876828737592046803 vs primary 0.9445962380876828726150498456; other four differences same ~1e-18 order). Independent `foreign_notional_mismatches=0` with source threshold 0.000001 per fill. Thus report **VWAP NUMERIC CLOSE, NOT BITWISE EQUAL**. Independence limited to different reader implementations, same source file and dataset; does not prove OOS utility, trade fill/PnL, or complete minute-row agreement (v2 does not independently hash every feature row).

#19/#22 now **SECOND_DAY_INDEPENDENT_SUMMARY_QC_5_OF_5_PASS**, still `RESEARCH-READY` conditional on §21 per-pair passport, full minute-row independent spot checks and formal readiness gate. No Strategy/DB/deploy/LIVE modification.


## 2026-10-19 — #19/#22 second-day all-minute independent exact hash QC PASS 5/5

CHECKED HERE: new separately coded read-only independent `research/microstructure/experiments/range_vwap_minute_row_independent_v1.py` source commit `876ccaa41cb1962c3513b27764f1fc64fe0ceac8`, five `sudo -u cripta python3` successful rc=0 on 2026-05-20 Bybit public-trades archives. Computes all 1440 minute records per coin from grouped price×size fills, previous 20 completed minute close range vs current 20, accumulated per-price volume POC and running VWAP, then canonical JSON serialization SHA256. This is distinct source from primary `range_vwap_profile_v1.py`, and uses no imported primary algorithm. **Independent hashes match primary 2026-05-20 all-1440-minute row hashes byte-for-byte in all five coins** (as recorded at journal second-day primary execution).

| Instrument | Independent 1440-minute-row SHA256 (exact match primary) | Range-valid count | QC |
|---|---|---:|---|
| APTUSDT | db43bdc14a4054bd03d1252010a96b8f7ce7be6db9128631231c8977d806c0de | 1401 | PASS |
| INJUSDT | 34e751c86f85f47773eb39fe7b0faaacacc7892120948b914b97ed2480ea59f3 | 1401 | PASS |
| DOTUSDT | 226e018fae1b43b11c7d7c230db868b59acbcaebc8e9564cd205ffdb5f0efac6 | 1277 | PASS |
| LTCUSDT | 33f3bacef04cfb3dd38407cb4940c8b7c2555057929ad05cf7635b03c1e56797 | 1010 | PASS |
| ARBUSDT | fa8e685c1960e5fb6b85280b40ce0f27fb8908ee3da821b343d21b969f3a2c9c | 1401 | PASS |

This resolves the pending **independent full-minute-row agreement** for the five 2026-05-20 #19 and #22 physical measurement streams. It is not a proof of separate exchange feed capture nor of profitable OOS signals. Original 2026-05-19 passport is historical; no silent retroactive mutation. Formal §21 per-instrument version/date passport and OOS/latency must be assessed independently; no `RESEARCH-READY` promotion at this step. No Strategy, DB, deploy or LIVE change.


## 2026-10-19 — #19/#22 second-day exact input integrity sealed 5/5

CHECKED HERE: read-only historical independent QC `range_vwap_independent_qc_v2.py` source pinned `fa683e2a27e50a1a18360c5c894d1ab2352bdf4a`, actor `cripta`, five successful second-day 2026-05-20 UTC source-file reads. Every gzipped public-trades source SHA256, byte count, monotonic trade timestamp check and trade `foreignNotional` consistency check registered in `research/microstructure/passports/range_vwap_2026-05-20_independent_v1.json` via commit `4c7730bb1dfb9f3d70fa81949c6149ebbd96661f`.

| Coin | Raw compressed source SHA256 | Bytes | Trades | Unsorted | Notional mismatches |
|---|---|---:|---:|---:|---:|
| APTUSDT | 6a589dc5afbc064a577f9d08bb921008e1518da42540fb5c15e4ea05a1144b02 | 4549721 | 117069 | 0 | 0 |
| INJUSDT | 7e2d2716b34167d65b43ca64fcd767aae902267f3d5f20ca3d2e90a3066a5a6e | 9464084 | 260994 | 0 | 0 |
| DOTUSDT | 39831bb5b07974f5de407efa8a2aa8ef2b32f257d67796aa7dc532b680fd477f | 5656864 | 151067 | 0 | 0 |
| LTCUSDT | b6e784e82918cf0e2c7b9f7d9464dc49478c7077eff0317f7a50be27764ee84e | 3213522 | 93645 | 0 | 0 |
| ARBUSDT | 7d8de412077e6f51eb46d1b3e1055af43dd228ec3d24da41a36c9e5f16cb254a | 8019377 | 209152 | 0 | 0 |

All five full-minute independent row hashes already matched byte-for-byte in prior checkpoint. **This closes previously open raw source SHA256 for May 20;** still do not call formal `RESEARCH-READY` until §21.6 fields and exact version/scope for each paired metric fully certified, including as-of availability and retained cube/result QC. No strategy or LIVE mutation.


## 2026-10-19 — next group #1/2/3/5/6/7 second-day full-day calculation 5/5

CHECKED HERE: `research/microstructure/experiments/trade_minute_factors_v1.py` from exact GitHub main source commit `c007d5aff378bfaced2e0d1514b8cd625e2132bb`; five successful read-only `cripta` executions on UTC `2026-05-20`, `--summary-only` (1440 records computed internally per coin, not stored on server), metrics returned `trade_rate,trade_rate_accel,buy_sell_delta,CVD,delta_accel,RVOL`. This expands six already historical-pilot indicators to a separately checked next calendar day, not a new Strategy signal.

| Instrument | Source trades | Active minutes | RVOL eligible minutes | First/60th/last minute trade count |
|---|---:|---:|---:|---|
| APTUSDT | 117069 | 1440 | 1380 | 111/114/2 |
| INJUSDT | 260994 | 1440 | 1380 | 143/163/104 |
| DOTUSDT | 151067 | 1436 | 1380 | 208/139/23 |
| LTCUSDT | 93645 | 1423 | 1380 | 104/193/18 |
| ARBUSDT | 209152 | 1440 | 1380 | 262/192/36 |

These source trade counts agree with independently SHA256-verified exact gz files in the #19/#22 2026-05-20 passport; 60-minute prior-window RVOL warmup excluded, valid counts 1380/1440 per coin, not 1440 signal values. Feed-valid physical calculation executed across 5/5, but no independent second reader for these six factors, no minute-level output hashes, no per-feature registered §21 gate, OOS economics or after-fee returns. Status `SECOND_DAY_CALC_5_OF_5, RESEARCH_READY_PENDING`. No trading/DB/deploy/LIVE mutation.


## 2026-10-19 — next group #4 / #8 and trade-distribution #27/#28 second-day 5/5

CHECKED HERE: two independent read-only existing GitHub-main source readers pinned at `e542682ce64ca06bf934da76e8b373ac918d1f88` and run as actor `cripta` for five symbols on UTC 2026-05-20 (all 10 invocations rc=0):
- `research/microstructure/experiments/large_trade_streaks_v1.py`: rolling as-of large-trade share and same-side fill-streak descriptive measurement, supporting checklist #4 Large Trade Share / #8 Trade Streaks.
- `research/microstructure/experiments/trade_distribution_v1.py`: descriptive fill-size distribution and inter-fill timestamp evidence, supporting #27 Inter-Trade Duration / #28 Trade-Size Distribution. Full-day quantile p95 is **ex post**; not as-of a trading feature.

| Symbol | Count of same-side streak episodes | Median streak length (fills) | Large-share eligible minutes | Full-day trade fills | Ex-post p95 single fill notional USDT |
|---|---:|---:|---:|---:|---:|
| APTUSDT | 8975 | 2 | 1380 | 117069 | 367.269504 |
| INJUSDT | 20642 | 2 | 1380 | 260994 | 657.0252 |
| DOTUSDT | 8768 | 2 | 1376 | 151067 | 515.7693300000001 |
| LTCUSDT | 6584 | 2 | 1363 | 93645 | 2042.052 |
| ARBUSDT | 12752 | 2 | 1380 | 209152 | 534.880566 |

These are distinct source-executed physical measurements, not independently cross-implemented minute-row equivalence, no full independent §21 passport or predictive OOS edge. Co-timestamp fills do not establish unique parent orders and may create zero-time bursts. No inferred universal trade threshold; quote-currency amounts are raw descriptive observations. Readiness for #4,8,27,28 remains OPEN, while second-day physical coverage is 5/5. No Strategy/DB/deploy/LIVE change.


## 2026-10-19 — #9 Queue Imbalance / #11 Depth / #29 Spread: bounded five-symbol orderbook QC

CHECKED HERE: source `research/microstructure/experiments/book_minute_three_v1.py` at immutable GitHub commit `e238c71cbe19e2e89669c946e3b2117ef4ed7d54`; actor `cripta`, Bybit 2026-05-19 UTC first 10 minutes, 5 independent rc=0. Reconstruct snapshot + delta; compute at each book message near-mid ±5bps bid and ask quote-depth, imbalance, top-of-book spread, keep last known snapshot state per completed minute. Book message counts reconcile with prior timestamp QC pilot 5/5. Ten completed-minute states per coin, one snapshot each. These are **pilot QC**, not independent implementation nor second day.

| Instrument | Messages incl snapshot | Completed minute states | Median bid/ask spread bps | Median ±5bp near-mid imbalance | Max latest book message age before minute close (ms) |
|---|---:|---:|---:|---:|---:|
| APTUSDT | 4826 | 10 | 1.0549 | -0.2998 | 469 |
| INJUSDT | 5562 | 10 | 1.9374 | 0.0328 | 17 |
| DOTUSDT | 4862 | 10 | 0.7987 | -0.0882 | 273 |
| LTCUSDT | 4378 | 10 | 1.8398 | -0.0536 | 281 |
| ARBUSDT | 4548 | 10 | 0.8498 | -0.0878 | 389 |

Important: near-mid ±5bp depth is a **specific bounded book estimator**, NOT all-depth liquidity or executable fill liquidity. Book timestamps are not independently validated observed/publication time. No as-of backtest, independent full-minute checksum, formal §21 passport or after-fee PnL. Historical full-day pilot is a separate earlier evidence record. #9/#11/#29 remain OPEN for new canonical readiness gate. No LIVE/Strategy/DB mutation.


## 2026-10-19 — #9/#11/#29 expanded first-60-minute cohort 5/5

CHECKED HERE: immutable `book_minute_three_v1.py` source pinned `e238c71cbe19e2e89669c946e3b2117ef4ed7d54`, read-only actor `cripta` five separate rc=0 first hour 2026-05-19 UTC, all five 60/60 last-observation-per-minute states present, one initial snapshot per symbol, no claim of entire day.

| Symbol | Book messages | Minute states | Median spread (bps) | Median near-mid ±5bps imbalance | Maximum book state age at minute close (ms) |
|---|---:|---:|---:|---:|---:|
| APTUSDT | 27262 | 60 | 1.0519 | -0.0678 | 568 |
| INJUSDT | 34093 | 60 | 1.8857 | 0.0304 | 316 |
| DOTUSDT | 27982 | 60 | 0.798 | -0.0855 | 572 |
| LTCUSDT | 23561 | 60 | 1.8391 | 0.078 | 481 |
| ARBUSDT | 26974 | 60 | 0.8462 | 0.0369 | 488 |

FINDING: imbalance medians can change sign or magnitude from first 10m to first 60m (LTC -0.0536 → +0.078), so never transfer one sampling-window metric into a universal signal threshold. Scope is bounded physical per-symbol book state, **not independent reader or formal §21 RESEARCH-READY**. No trading/live changes.


## 2026-10-19 — #9 / #11 / #29 full UTC day physical book-minute measurement 5/5

CHECKED HERE: independent successful bounded read-only `sudo -u cripta python3` source `research/microstructure/experiments/book_minute_three_v1.py` pinned GitHub source commit `c0f16233c14d9ba436cfe084b6881f081ec51c34` executed separately for five Bybit ob200 files UTC 2026-05-19 00:00–24:00 with `--minutes 1440 --summary`. All rc=0, each 1440/1440 nonempty completed-minute states and 2 snapshots. Event counts reconcile with previous inventory: snapshots + delta events, 5/5 QC PASS. Near-mid depths are ±5bps quote-notional based on current reconstructed book, not all-book depth or executable liquidity.

| Coin | Total book events | Book snapshots | Completed-minute states | Median top spread bps | Median ±5bps near-mid imbalance | Max minute-end observation age ms |
|---|---:|---:|---:|---:|---:|---:|
| APTUSDT | 606837 | 2 | 1440 | 1.0636 | 0.0235 | 2468 |
| INJUSDT | 733303 | 2 | 1440 | 2.0103 | -0.0024 | 917 |
| DOTUSDT | 631778 | 2 | 1440 | 0.8102 | 0.0166 | 1473 |
| LTCUSDT | 542094 | 2 | 1440 | 1.8459 | 0.0484 | 1881 |
| ARBUSDT | 616466 | 2 | 1440 | 0.8656 | -0.0360 | 1088 |

FINDING: daily imbalance median shifts signs relative to previous first-hour values for several coins; window scope cannot be transferred unexamined. This is a full-day one-reader physical measurement with correct per-coin source-count reconciliation, NOT independent full-minute hash QC or §21.6 research readiness. Clock field `ts` is source reported; publication / availability semantics not externally proven. Do not promote to Strategy or report after-fee return. No trading/DB/deploy/LIVE mutation.


## 2026-10-19 — #9/#11/#29 independent Decimal QC first hour 5/5

CHECKED HERE: source `research/microstructure/experiments/book_spread_imbalance_independent_v1.py` independently implemented with Decimal-priced reconstructed bids/asks, price×size depth, minute-last values. Exact GitHub source commit `68f665348b48796a03d9eba36dd7edbe2dab523e`, five successful read-only `cripta` runs on 2026-05-19 UTC first 60 minutes. Results exactly agree **at the reported four-decimal summary precision** with earlier `book_minute_three_v1.py` first-hour outputs for all five coins (including book message count, 60/60 minute coverage, median spread and median ±5bp near-mid imbalance).

| Coin | Events | States | Spread bps | Imbalance | QC |
|---|---:|---:|---:|---:|---|
| APTUSDT | 27262 | 60 | 1.0519 | -0.0678 | PASS |
| INJUSDT | 34093 | 60 | 1.8857 | 0.0304 | PASS |
| DOTUSDT | 27982 | 60 | 0.7980 | -0.0855 | PASS |
| LTCUSDT | 23561 | 60 | 1.8391 | 0.0780 | PASS |
| ARBUSDT | 26974 | 60 | 0.8462 | 0.0369 | PASS |

The independent check is **summary-level for first hour** only. It does not yet compare unrounded per-minute values or prove 1440-minute agreement; it also does not establish physical exchange publication latency or §21 full passport readiness. Source book depth is within ±5bps, not all visible liquidity. No trading/DB/deploy/LIVE changes.


## 2026-10-19 — #9/#11/#29 independent full-day Decimal summary QC 5/5

CHECKED HERE: independent `research/microstructure/experiments/book_spread_imbalance_independent_v1.py` pinned to GitHub commit `68f665348b48796a03d9eba36dd7edbe2dab523e`, standard read-only `cripta` actor, full Bybit UTC 2026-05-19 day, all five completed successfully. INJ/DOT long runs returned asynchronous SentinelX job ids and were verified completed successfully from `notifications_check` with exit_code=0, never restarted or polled in a loop. These five independent full-day Decimal-calculation rounded summary medians and inventory counts exactly agree with primary `book_minute_three_v1.py` full-day observations in earlier checkpoint `5aaf62b05d12530b52e6f2bd7836909f003a0848`.

| Symbol | Book messages | Snapshots | Completed minute states | Median spread bps | Median near-mid ±5bps imbalance | Full-day independent summary QC |
|---|---:|---:|---:|---:|---:|---|
| APTUSDT | 606837 | 2 | 1440 | 1.0636 | +0.0235 | PASS |
| INJUSDT | 733303 | 2 | 1440 | 2.0103 | -0.0024 | PASS |
| DOTUSDT | 631778 | 2 | 1440 | 0.8102 | +0.0166 | PASS |
| LTCUSDT | 542094 | 2 | 1440 | 1.8459 | +0.0484 | PASS |
| ARBUSDT | 616466 | 2 | 1440 | 0.8656 | -0.0360 | PASS |

QC scope is source-count/snapshot/minute count and **rounded summary median equality**. This is NOT independent byte-for-byte full-1440-row equality nor exact independent per-minute depth equality; those remain OPEN. Public/exchange source clocks and publication/availability semantics remain unverified; no causal as-of trading signal or after-fee profitability assessment. Do not promote #9/#11/#29 to §21.6 RESEARCH-READY solely on this evidence. No DB/service/Strategy/LIVE mutation.


## 2026-10-19 — #18/#20 second-day extension partial, OpenAI upstream security hard stop

CHECKED HERE: existing `research/microstructure/experiments/price_minute_features_v1.py` fetched as immutable GitHub source at `6a3c07acc9832165f51d33a1e5d534fc7441b591`; standard actor `cripta` read-only and python command used to invoke date-selectable `calculate(symbol, "2026-05-20")` and summarize 1440 rows. Three individual server runs returned rc=0:

| Symbol | Fills | Minutes | Valid 60-minute lag-1 autocorrelation | Valid prior60 high | Above prior high | Below prior low |
|---|---:|---:|---:|---:|---:|---:|
| APTUSDT | 117069 | 1440 | 1380 | 1439 | 74 | 77 |
| INJUSDT | 260994 | 1440 | 1380 | 1439 | 81 | 87 |
| DOTUSDT | 151067 | 1440 | 1380 | 1439 | 65 | 73 |
| LTCUSDT | NOT EXECUTED | — | — | — | — | — |
| ARBUSDT | NOT EXECUTED | — | — | — | — | — |

The next call was **blocked by OpenAI upstream tool-security system**. This is not a SentinelX `command_not_allowed`, Unix EACCES or confirmed host Python error; its request did not yield a server result. In accordance with `DEVELOPMENT_RELEASE §19.5–19.6`, no replay, alternative actor, obfuscated command or workaround was attempted. **HARD_STOP=YES for this attempted route; OWNER_DECISION_REQUIRED=YES before further execution via any newly approved route.** Physical second-day #18/#20 cohort status **PARTIAL_3_OF_5**. Previously verified #9/#11/#29 independent full-day summary QC 5/5 remains independently valid. No claims of new trading advantage or after-fee P&L, no Strategy/deploy/DB/LIVE modifications.


## 2026-10-19 — nine indicator proxy/identity GitHub audit, immutable #10 source recovered

CHECKED HERE: GitHub `main` tree and current source definitions for #10, #12, #13, #14, #15, #16, #17, #21, #30; machine-readable gap audit `research/microstructure/passports/proxy_identity_gate_audit_2026-10-19_v1.json` first committed `082df27f903d57b6b51758652af2a7a0eb7fcec2`. All nine have existing current-main physical/proxy source, but strict names not silently inferred from proxies. #14/#17 incorporate future labels, never as-of signals; #12 cannot attribute cancellation; #16 passive absorption unproven; #21 true liquidity sweep unproven; #30 same-order refill unproven. Per-coin §21 formal readiness still OPEN.

**#10 source identity:** current main has `rank_ofi_v1.py` and `book_top5_membership_v1.py`, but not `top1_price_transition_ofi_v1.py` named in historical day journal. The latter was positively fetched by exact **immutable historical GitHub commit** `f5485b7cb729195e8ef6ea394d31f9ae94acf444`: `research/microstructure/experiments/top1_price_transition_ofi_v1.py`, whose header defines exact best-price-transition top-of-book OFI. Recorded historical source pointer in audit commit `c6734dfe1ab651f2ce0619ad010fff507314cdd1`. Recovery resolves locating historical source, NOT algorithm equivalence to current `rank_ofi_v1.py`, NOT strict multi-level price-aware OFI.

No server tools, filesystem writes or alternate interpreter invoked. #18/#20 upstream-blocked route not retried. Trading/DB/deploy/LIVE unchanged.
