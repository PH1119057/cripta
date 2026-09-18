# Отчёт прохода 2 — Mainnet safety и arming ticket

Дата: 13 августа 2026.

## Результат

Граница Mainnet execution переведена с доверия к полям торгового intent на проверку
свежего exchange-owned состояния. `MainnetMutation` теперь содержит только endpoint,
payload, вид операции и idempotency key. Стратегия больше не может объявить собственные
notional, total exposure, daily loss, leverage, margin mode или наличие стопа.

Короткоживущий билет Micro-Live:

- живёт только в памяти и действует не более пяти минут;
- связан с выбранным endpoint, `BotW-Mainnet`, главным UTA 2.0 аккаунтом, одним
  символом, стратегией, её версией и fingerprint параметров;
- требует включённый внешний live switch, типизированное положительное решение
  historical gate и свежие Public WS / Private WS / REST данные;
- требует complete account-wide positions/open-orders и reconciliation;
- блокирует Spot, Options/USDC, Wallet и неизвестные лишние permissions;
- после перезапуска отсутствует, а после истечения блокирует новые входы.

Перед каждым входом gateway независимо проверяет instrument metadata, min notional,
tick/qty precision, лимитный тип, `orderLinkId`, attached Full/MarkPrice/Market stop,
направление stop/TP, UTA 2.0, isolated, one-way и 1x. Экспозиция вычисляется по позиции,
всем открытым заявкам и новому ордеру; дневной лимит включает realized PnL и текущий
отрицательный UPL. Конкурентные submit сериализованы.

Для kill switch остаются только cancel и корректный exchange `reduceOnly`. Перенос
server stop в сторону увеличения риска запрещён. Автоматические set-leverage и
switch-isolated удалены из Mainnet транспорта. Full `LIVE` пока не активируется.

## Проверка

- Отрицательные тесты: подмена symbol/category/positionIdx, Market entry, off-step,
  forged `orderLinkId`, неверный stop, лишние payload-поля, stale state, смена ключа,
  чужие позиции/ордера, forged exposure/PnL, over-close и конкурентные входы.
- Ruff и strict mypy: без ошибок.
- Контейнерная регрессия: `214 passed, 2 skipped`; GUI пропущен только из-за
  отсутствующей системной `libEGL`, soak вынесен отдельно.
- Отдельный soak: `10 000` циклов, `1 passed`.
- Headless vertical smoke: успешно.
- Реальные REST/WebSocket запросы, API-ключи и торговые POST не использовались.

## Следующий проход

Проход 3 подключит этот gateway к одному Mainnet coordinator и desktop workflow. До
этого Micro-Live недоступен из UI, что является намеренным fail-closed состоянием.
