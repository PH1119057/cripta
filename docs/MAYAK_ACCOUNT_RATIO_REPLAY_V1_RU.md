# MAYAK — historical Long/Short account ratio replay

**Версия:** 1.0 · 2026-09-06

Exact public Bybit `/v5/market/account-ratio`, period 5min. `buyRatio` хранится как `long_ratio`, `sellRatio` как `short_ratio`. Это доля аккаунтов, а не денег/капитала. Для frozen T используется только последняя запись `timestamp <= T`; outcome/PnL не подаётся в MAYAK. Research использует raw ratios и `long_short_imbalance=long-short`; direction-adjusted imbalance существует только во внешнем Analyst. Trading effect: NONE.
