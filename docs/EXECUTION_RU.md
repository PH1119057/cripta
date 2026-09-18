# EXECUTION — активный контракт исполнения

**Версия:** 1.0  
**Дата:** 2026-09-18  
**Статус:** активный канонический документ слоя EXECUTION

# 1. Назначение

Execution — техническая граница между уже принятым торговым решением и внешней
торговой площадкой.

# 2. Вход Execution

Execution получает `ExecutionRequest` с точной lineage:
- signal_id;
- strategy_id/version/fingerprint;
- entry_plan_fingerprint;
- attempt/decision identity;
- symbol/direction;
- Strategy-owned execution/capital/protection parameters.

# 3. Запрет собственной торговой логики

Execution не выбирает Strategy, не меняет Entry formula, не вычисляет H9/H3 как
собственную policy, не подставляет 130 bars/9h/3h/stop/leverage/TTL как
глобальный торговый default, не переоценивает MAYAK/Dispatcher и не решает,
что LONG лучше SHORT.

Если нужная Strategy-owned policy отсутствует или unsupported — fail-closed.

# 4. Обязанности

Execution отвечает за:
- validation exact identities/fingerprints;
- exchange adapter;
- order preparation/submission;
- idempotency;
- client/exchange IDs;
- fill truth;
- fees/slippage where measurable;
- initial protection;
- retries без двойной мутации;
- reconciliation;
- durable handoff;
- recovery after restart;
- technical fail-closed.

# 5. Exchange-agnostic

Bybit является текущим подключённым провайдером.
Архитектура Execution должна позволять другие биржевые адаптеры без изменения
Strategy/Entry semantics.

# 6. Operational safety

Неизвестная позиция, stale private state, потеря reconciliation, неизвестный
fill/qty/protection или owner kill имеют право технически остановить mutation.

Это safety, а не новая оценка рынка.
