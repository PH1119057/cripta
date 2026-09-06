# MAYAK V2 — exact historical OI positioning replay

**Версия:** 1.0
**Дата:** 2026-09-06
**Статус:** implementation/research contract

## 1. Цель

Причинно восстановить OI-компонент `CoinMarketContext.positioning` на frozen Entry touch без использования будущего outcome.

```text
MAYAK_TRADING_EFFECT = NONE
OUTCOME_USED_BY_REPLAY = NO
SOURCE = BYBIT_V5_OPEN_INTEREST_5M
```

## 2. Источник

Используется публичный historical endpoint Bybit V5 `open-interest`, `category=linear`, `intervalTime=5min`. Compact source-файлы обязаны хранить timestamp/open_interest, SHA256 и manifest с endpoint/period/source commit.

5m history является точной историей API-точек, но не выдаётся за tick-by-tick live ticker history.

## 3. Один feature engine

Исторические OI points подаются как `TICKER` events в тот же `CausalMayakReplay -> LiveMayakEngine.on_ticker()`. Отдельная формула OI 5/15/30/60m в research-коде запрещена.

На каждом frozen signal T replay видит только OI events `event_at <= T`. Для current-vs-prior 60m сохраняется минимум 120m pre-roll.

## 4. Outputs

- `MAYAK_POSITIONING_CONTEXTS.jsonl`;
- `MAYAK_POSITIONING_FEATURES.csv`;
- `P34_EQUIVALENCE.json`;
- `RUN_MANIFEST.json`.

Ни один output positioning replay не содержит Entry outcome.

## 5. P34 regression oracle

Старые `cross_asset_validation/<SYMBOL>.../p34/signals_open_interest.csv` не являются входом MAYAK. После нового replay они используются только для независимого сравнения `oi_change_5m/15m/30m/60m`.

Mismatch не разрешается tolerance-изобретением после просмотра результата: report обязан сохранить count, compared и max_abs_diff; любое материальное расхождение требует forensic.

## 6. Research boundary

Positioning values присоединяются к Strategy outcomes только внешним Analyst/component research после завершения immutable objective replay. Никаких OI veto/allow или CoinMarketRating этим этапом не создаётся.
