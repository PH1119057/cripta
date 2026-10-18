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
