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
