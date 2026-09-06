# MAYAK V2 — exact historical funding / mark-index basis replay

**Документ:** `MAYAK_BASIS_FUNDING_REPLAY_V1_RU.md`
**Версия:** 1.0
**Дата:** 2026-09-06
**Статус:** implementation/research contract

Верхние контракты:

- `MAYAK_OBJECTIVE_CONTEXT_V2_IMPLEMENTATION_RU.md`
- `MAYAK_HISTORICAL_SIGNAL_BACKFILL_RU.md`
- `MAYAK_COMPONENT_RESEARCH_V1_RU.md`
- `PROJECT_GOVERNANCE_RU.md`

## 1. Цель

Причинно восстановить для frozen ALL9 / 1063 сигналов объективные поля MAYAK:

```text
funding_rate
funding_rate_change_from_previous
mark_price
index_price
mark_index_premium_pct
```

Это отдельный objective source/replay слой. Он не меняет Strategy/Entry и не получает outcome как вход.

```text
MAYAK_TRADING_EFFECT = NONE
ENTRY_POLICY_CHANGED = NO
OUTCOME_USED_BY_REPLAY = NO
THRESHOLD_SELECTION = NO
```

## 2. Funding source

Funding загружается из публичного historical funding endpoint подключённого exchange adapter для linear instruments.

Каждая запись хранится как:

```text
timestamp
funding_rate
```

Событие считается доступным не раньше фактического `fundingRateTimestamp`.

Для корректного `funding_rate_change_from_previous` source period обязан включать минимум один предыдущий funding interval до первого frozen signal.

## 3. Mark / index source

Mark/index используются из compact 5m historical datasets, полученных проверенным P36-compatible downloader.

В source CSV timestamp означает opening timestamp 5m candle, а поле `close_price` — её close.

Поэтому causal availability:

```text
available_at = candle_open_at + 5 minutes
```

Запрещено подавать close в MAYAK в момент открытия свечи.

## 4. Один движок

Replay не вычисляет premium собственной формулой. Он подаёт causal TICKER events в:

```text
CausalMayakReplay
-> LiveMayakEngine.on_ticker()
-> CoinMarketContext.positioning
```

Из snapshot извлекаются только фактически рассчитанные поля текущего MAYAK.

## 5. Last-price premium

`last_index_premium_pct` и `last_mark_premium_pct` не восстанавливаются этим проходом, если exact historical ticker/last-price event не подан в `LiveMayakEngine`.

Entry price не используется как подмена market ticker: trading signal не является входом MAYAK.

## 6. Frozen identity

Используется тот же frozen baseline:

```text
/srv/cripta/research_runs/minute_entry_book_v1/inputs/eo1_events.csv
scenario = BASELINE_0P00
signals = 1063
```

Ключ:

```text
symbol + direction + touch_at
```

## 7. Provenance

Source backfill manifest хранит:

- project/source commit;
- exact period;
- symbols;
- endpoint semantic label;
- downloader code SHA256;
- per-file SHA256 и row counts;
- `outcome_used=false`.

Basis replay manifest хранит:

- baseline SHA256;
- source commit;
- mark/index source manifest SHA256;
- funding source manifest SHA256;
- replay code SHA256;
- 1063 signal count;
- causal availability policy;
- `outcome_used_by_replay=false`;
- `trading_effect=NONE`.

## 8. Research boundary

После immutable objective output Analyst может присоединить `basis_*` поля к frozen outcome correlation.

Разрешены только заранее зафиксированные univariate metrics из `MAYAK_COMPONENT_RESEARCH_V1_RU.md`.

Запрещено на этом проходе:

- искать cutoff;
- строить Entry gate;
- подбирать комбинацию premium/funding по outcome;
- менять MAYAK formula по результату;
- называть старый HOLDOUT7 новым OOS.
