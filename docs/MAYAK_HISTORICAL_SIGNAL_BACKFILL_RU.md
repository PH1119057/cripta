# MAYAK — причинный исторический backfill по frozen Entry сигналам

**Документ:** `MAYAK_HISTORICAL_SIGNAL_BACKFILL_RU.md`
**Версия:** 1.0
**Дата:** 2026-09-06
**Статус:** implementation/research contract
**Основание:** решение владельца 2026-09-06 выполнить исторический replay нового MAYAK V2 без ожидания недель live-наблюдения.

Верхние контракты:

- `../CRIPTA_ARCHITECTURE_RULES_RU_V1.md`
- `MAYAK_ARCHITECTURE_PRINCIPLES_RU.md`
- `MARKET_CONTEXT_AND_COIN_RATING_ARCHITECTURE_RU.md`
- `MAYAK_OBJECTIVE_CONTEXT_V2_IMPLEMENTATION_RU.md`
- `M3_ENVIRONMENT_RESEARCH_PROTOCOL_RU.md`

## 1. Цель

Воспроизвести, что `mayak-v2.2 / objective-coin-context-v2` объективно видел бы в моменты frozen Entry V1 сигналов, если бы текущий движок существовал в период 2026-05-18..2026-08-16.

Это не backtest новой Strategy и не разрешение MAYAK влиять на торговлю.

```text
MAYAK_TRADING_EFFECT = NONE
DISPATCHER_TRADING_EFFECT = NONE
ENTRY_POLICY_CHANGED = NO
OUTCOME_USED_BY_REPLAY = NO
```

## 2. Frozen signal identity

Авторитетный вход текущего прогона:

`/srv/cripta/research_runs/minute_entry_book_v1/inputs/eo1_events.csv`

SHA256 на контрольной точке 2026-09-06:

`91044aba6f3148e6599a5ce9a7a1414126d19a9cbed28983e19f753203b1d44f`

Фильтр `scenario=BASELINE_0P00` обязан дать ровно 1063 уникальных ключа:

```text
symbol + direction + touch_at
```

## 3. Outcome присоединяется только после replay

Основной untouched outcome `+1.10% vs -1.00%`:

`/data/cripta/legacy/cripta1_snapshot_20260822/reports/untouched_minus1_plus110_v1/ALL9_20260820_162740/event_results.csv`

SHA256:

`9157a1bb62fef561c937686ddb53f5581529f2a89b93ab7c8d527f39e0c3e919`

Контрольный состав: 594 `reached_plus_1p10`, 460 `hit_minus_1p00`, 9 `data_end`.

Для `+0.10% vs -1.00%` используется P47J activation audit:

`/data/cripta/legacy/cripta1_snapshot_20260822/reports/early_protection_minus01_v1/ALL9_20260819_103850/event_results.csv`

SHA256:

`d06337296a86fec00cfc46fa61674321e6583d202c4903b37c1217d10ba7f2ec`

Семантика восстановления:

- baseline key отсутствует в P47J event table -> frozen initial `-1%` раньше `+0.10%`;
- есть `activation_at` -> `+0.10%` достигнуто раньше initial stop;
- строка есть, но `activation_at` отсутствует -> unresolved/data end.

P47J summary фиксирует 66 old initial stops, 995 activated `+0.10%`, 2 data-end no activation. Нельзя молча заменять это более ранней округлённой оценкой.

Outcome-файлы не передаются в `CausalMayakReplay`; они присоединяются внешним коррелятором только после создания MAYAK context.

## 4. Exact historical layers первого прохода

Для ALL9 доказанно доступен raw derivatives public trade tape:

`/data/cripta/datasets/raw/20260518_20260816/<SYMBOL>/public_trades/*.csv.gz`

Первый exact replay включает:

```text
derivatives executed buy/sell/net/turnover
flow 1/5/15/30/60m
flow speed / acceleration
large-trade share
price return 1/5/15/60m
market breadth / synchronization agreement
relative strength vs selected panel / BTC / ETH
```

Первый проход НЕ выдумывает отсутствующие слои:

```text
spot = NO_DATA
OI = NO_DATA
funding = NO_DATA
mark/index premium = NO_DATA
orderbook = NO_DATA_FIRST_PASS
liquidations = NO_DATA_EXACT_SOURCE_NOT_AVAILABLE
historical transport = NO_DATA
```

Существующие P34/P40 исследования могут позднее использоваться как отдельные внешние causal evidence, но не подменяют exact input `LiveMayakEngine` без специального совместимого адаптера.

## 5. Один движок

Исторический расчёт не имеет собственной feature-математики.

```text
raw archived event
  -> CausalMayakReplay
  -> LiveMayakEngine
  -> snapshot at signal T
```

Все raw trade events проходят только если `event_at <= signal_at`.

## 6. Retention contract

Поскольку flow context сравнивает текущее 60m окно с предыдущими 60m, `TradeWindow` обязан удерживать минимум 7200 секунд событий. Retention 3600 секунд недостаточен и делает previous-60m acceleration/turnover ratio неполными.

Этот контракт одинаков для live и replay.

## 7. Блоки и checkpoint

Полный прогон делится на причинно независимые календарные блоки. По умолчанию 7 суток + 120 минут pre-roll.

120 минут нужны одновременно для:

- текущего 60m окна;
- предыдущего 60m окна.

Каждый block хранится отдельно и может быть переиспользован только при совпадении:

- script version;
- source commit;
- SHA256 фактического replay-кода (`live.py + objective_replay.py + historical_signal_backfill.py`);
- explicit panel symbols;
- exact signal keys.

## 8. Почему block replay допустим

`CoinMarketContext` использует rolling windows максимум 120 минут исходной истории с учётом current-vs-prior 60m flow. Поэтому 120m pre-roll достаточен для per-coin context.

Поле `direction_synchronization.change` на первом snapshot блока не имеет предыдущего snapshot вне блока и не считается exact comparative feature. Текущий `agreement`, price breadth, market state и `CoinMarketContext` от этого не зависят.

## 9. Replay panel

Panel всегда передаётся явно через `--symbols`.

Для frozen ALL9:

```text
UNIUSDT
LINKUSDT
BTCUSDT
ETHUSDT
XRPUSDT
1000PEPEUSDT
SOLUSDT
DOGEUSDT
ADAUSDT
```

Это historical research panel, а не текущий live eligibility list.

## 10. Outputs

Минимум:

```text
blocks/*.json
MAYAK_SIGNAL_CONTEXTS.jsonl   # без outcome внутри MAYAK context
MAYAK_ENTRY_CORRELATION.csv   # внешний join context + frozen outcomes
SUMMARY.json
RUN_MANIFEST.json
```

`RUN_MANIFEST.json` обязан хранить project commit, SHA256 фактического replay-кода, входные SHA256, explicit symbols, период, block/workers, список используемых raw archives, SHA256 исходного `MANIFEST.sha256.json`, content fingerprint требуемых raw archives, metadata fingerprint, exact/no-data layers и `outcomes_used_by_replay=false`.

## 11. Исследовательская граница

После backfill допускается измерять, какие объективные MAYAK-компоненты различают хорошие/плохие исходы Entry.

Запрещено на этом этапе:

- менять MAYAK по PnL;
- оптимизировать формулу CoinMarketRating внутри MAYAK;
- менять Entry;
- открывать новый holdout для подбора порогов без отдельного протокола;
- выдавать signal-level correlation за portfolio backtest.

Путь остаётся:

```text
HISTORICAL CONTEXT
-> COMPONENT RESEARCH
-> OOS / robustness
-> owner decision
-> Dispatcher/Strategy version if evidence exists
```
