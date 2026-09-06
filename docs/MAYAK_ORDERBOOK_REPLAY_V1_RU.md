# MAYAK V2 — exact historical orderbook replay

**Документ:** `MAYAK_ORDERBOOK_REPLAY_V1_RU.md`
**Версия:** 1.0
**Дата:** 2026-09-06
**Статус:** implementation/research contract

Верхние контракты:

- `MAYAK_OBJECTIVE_CONTEXT_V2_IMPLEMENTATION_RU.md`
- `MAYAK_HISTORICAL_SIGNAL_BACKFILL_RU.md`
- `MAYAK_COMPONENT_RESEARCH_V1_RU.md`
- `PROJECT_GOVERNANCE_RU.md`

## 1. Цель

Причинно восстановить objective derivatives depth-200 liquidity context MAYAK на frozen ALL9 / 1063 signal touch без Entry/PnL как входа.

```text
MAYAK_TRADING_EFFECT = NONE
ENTRY_POLICY_CHANGED = NO
OUTCOME_USED_BY_REPLAY = NO
THRESHOLD_SELECTION = NO
```

## 2. Exact source

Источник — уже сохранённые raw orderbook archives:

```text
/data/cripta/datasets/raw/20260518_20260816/<SYMBOL>/orderbook/
YYYY-MM-DD_<SYMBOL>_ob200.data.zip
```

Каждый архив содержит websocket `snapshot + delta` с exchange event timestamp (`cts` при наличии), `u` и `seq`.

Content provenance берётся из канонического `MANIFEST.sha256.json`; фактический размер каждого прочитанного файла обязан совпасть с manifest entry.

Декодирование JSON может использовать ускоренный `orjson`, если он доступен в research environment. Это исключительно transport/performance backend: normalized payload и feature math не меняются, stdlib `json` остаётся обязательным fallback. `RUN_MANIFEST.json` фиксирует backend и его версию.

## 3. Reconstruction

Используются те же snapshot/delta semantics, что и в проверенном P40/Pilot reconstruction:

- snapshot заменяет всю книгу;
- delta изменяет только перечисленные levels;
- qty <= 0 удаляет level;
- до первого snapshot delta не считается валидным состоянием.

Для каждого прочитанного участка обязательна непрерывность update id `u` между snapshot-ами. Gap = fail-closed для exact replay.

## 4. Один MAYAK feature engine

Исторический reader не вычисляет liquidity features собственной формулой.

После восстановления full book state выбранные причинные состояния подаются в:

```text
CausalMayakReplay
-> LiveMayakEngine.on_book()
-> CoinMarketContext.payload.liquidity.derivatives
```

Именно `LiveMayakEngine` считает:

- best bid / best ask / mid;
- total bid/ask notional;
- imbalance;
- depth 5/10/25/50 bps;
- immediate bid/ask change;
- bid/ask change 1/5/15m;
- imbalance change 1/5/15m.

## 5. Sparse exact replay

Raw depth-200 может обновляться около 10 раз/сек. Вызывать Python `on_book()` на каждом delta не требуется для воспроизведения snapshot T.

Reader применяет каждый raw delta к reconstructed state, но хранит обратимые изменения только последних ~1000 секунд. В момент signal T:

1. определяется последний raw book event `E <= T`;
2. сохраняется непосредственное предыдущее raw состояние перед E для exact immediate change;
3. для `E-1m`, `E-5m`, `E-15m` выбирается ровно то состояние, которое сохранил бы `LiveMayakEngine.book_history`;
4. учитывается текущая live-семантика: внутри одной целой секунды `book_history` хранит только последний processed update;
5. выбранные состояния хронологически подаются в новый `LiveMayakEngine`;
6. snapshot формируется в T.

Контрактный тест обязан доказать:

```text
FULL_BOOK_FEED_LIQUIDITY_JSON == SPARSE_BOOK_REPLAY_LIQUIDITY_JSON
```

на потоке с несколькими updates в секунду.

## 6. Day boundaries

Orderbook raw начинается 2026-05-18. Для signal раньше ~00:16:40 может понадобиться tail предыдущего дня для 15m baseline. Такой предыдущий archive читается целиком, если существует.

Отсутствующий необходимый previous-day archive не превращается в zero; соответствующее baseline поле остаётся missing/NO_DATA и фиксируется в manifest.

## 7. Frozen identity

Baseline тот же:

```text
/srv/cripta/research_runs/minute_entry_book_v1/inputs/eo1_events.csv
scenario = BASELINE_0P00
signals = 1063
```

Ключ: `symbol + direction + touch_at`.

## 8. Outputs

```text
MAYAK_ORDERBOOK_CONTEXTS.jsonl
MAYAK_ORDERBOOK_FEATURES.csv
RUN_MANIFEST.json
```

Manifest хранит source commit, replay code SHA256, baseline SHA256, raw manifest SHA256, required archive entries/content hashes, bytes/records actually read, update-id continuity counters, exact/missing signal counts и `outcome_used_by_replay=false`.

## 9. Research boundary

После immutable replay Analyst может исследовать только заранее зафиксированные normalized liquidity features. Absolute USD depth не должен автоматически считаться переносимым pooled ALL9 признаком из-за разного масштаба инструментов.

Никакой результат этого прохода сам по себе не становится Entry gate или `CoinMarketRating`.
