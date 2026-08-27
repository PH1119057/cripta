# Чек-лист реализации Bybit Strategy Workbench

Обновлено: 14 августа 2026. Источник требований —
`CODEX_TASK_BYBIT_TRADING_SUBSYSTEM.md` и `crypto_bot_project_principles_ru.md`.

Обозначения:

- `[x]` — реализовано и покрыто автоматическими тестами;
- `[ ]` — ещё требуется;
- `Частично` — фундамент есть, но критерий приёмки целиком не закрыт.

## A. Фундамент

- [x] Структура Python-пакета, `pyproject.toml`, CLI и профили Replay/Mainnet.
- [x] Доменные модели на `Decimal`, UTC timestamps и явная машина состояний.
- [x] Mainnet read-only доступен без торгового разрешения; execution arming хранится
  только в памяти и после перезапуска всегда `SHADOW`/disarmed.
- [x] Детерминированные FakeExchange и ReplayEngine.
- [x] SQLite WAL, миграции v1–v7, reconciliation, durable Mainnet idempotency и audit trace.
- [x] Секреты в Windows Credential Manager; редактирование чувствительных полей.
- [x] Pydantic v2 на environment settings boundary и версионируемых risk-профилях.
- [x] Bybit DTO валидируются Pydantic v2 (strict/extra-forbid boundary).
- [x] Persistence использует единый SQLAlchemy 2.x ownership boundary поверх SQLite DB-API,
  сохраняя явные SQL-репозитории и миграции.
- [x] Windows CI-рецепт с Ruff, mypy, pytest и headless smoke.
- [x] Hypothesis suite для stop/sizing инвариантов (по 300 генерируемых примеров).

## B. Bybit read-only

- [x] Instrument metadata, candles, wallet, position и open orders через V5 REST.
- [x] Public kline/ticker и private wallet/position/order/execution WebSocket.
- [x] Mainnet endpoint выбирается явно: `api.bybit.com`, `api.bybit.kz` или ручной
  HTTPS override; скрытого fallback нет.
- [x] Freshness health, heartbeat, backoff, восстановление подписок.
- [x] Начальный REST snapshot и reconciliation перед состоянием READY.
- [x] Фоновый lifecycle не блокирует Qt.
- [x] Общий async rate limiter для read/write transport с 20% safety reserve.
- [x] Явная классификация clock skew, auth, rate limit, symbol halt и margin errors.
- [x] Account info, margin mode, position leverage и фактические maker/taker fee rates.
- [x] GET-only connection test: server time → query-api → balance → positions → orders.
- [ ] Подтверждённый пользователем connection test с реальным `BotW-Mainnet`.

## C. Risk и stop engine

- [x] Sizing по stop distance, equity, available balance, leverage и notional cap.
- [x] Tick/qty normalization, min qty/notional и safe rounding.
- [x] Loss limit, loss streak, cooldown, freshness, часы, symbol/direction limits.
- [x] Запрет усреднения/увеличения незащищённой позиции по умолчанию.
- [x] Fixed, Percent, Distance, ATR, trailing distance/percent policies.
- [x] Монотонность stop для long/short и запрет скрытого расширения риска.
- [x] Planned/requested/confirmed модель защиты.
- [x] Настройки risk-профиля из UI, Pydantic-валидация и версионирование в SQLite.
- [x] Funding/fee/slippage preview: ticker funding, account fee rate и risk-profile
  slippage; funding показан как оценка одного следующего интервала.
- [ ] Диалог явного разрешения расширения stop с пересчётом суммы риска.

## D. Replay и проверка на исторических данных

- [x] Закрытые свечи, строгая хронология и отсутствие look-ahead в ReplayEngine.
- [x] Market на следующей доступной цене, limit по пересечению, partial fills.
- [x] Gap through stop, консервативная ambiguous candle, fees, funding, seed.
- [x] Snapshot/restore открытой replay-позиции.
- [x] Валидация исторического набора свечей и воспроизводимый fingerprint данных.
- [x] Строгий CSV-импорт: timezone-aware время, точные Decimal, настраиваемые колонки,
  запрет неявной сортировки и fingerprint после валидации.
- [x] Опциональный Parquet-импорт, постраничная выгрузка Bybit и явный gap report.
- [x] Метрики: net/gross PnL, fees, funding, win rate, profit factor, drawdown и
  доля ambiguous trades.
- [x] Exposure/time-in-market и buy-and-hold benchmark comparison.
- [x] Строгий временной train/validation/test split без перемешивания.
- [x] Генератор walk-forward окон без пересечения train/test.
- [x] Универсальное исполнение Strategy через тот же RiskEngine и ReplayEngine на
  каждом walk-forward/out-of-sample окне; новый экземпляр стратегии на каждый прогон.
- [x] Защита от look-ahead: callback получает только закрытую свечу, а созданный entry
  может исполниться не раньше следующей свечи.
- [x] Stress-прогоны комиссии, проскальзывания, gaps и задержки исполнения.
- [x] Воспроизводимый persisted-отчёт: версия стратегии, параметры, период, fingerprint,
  execution costs, seed, метрики и checks.
- [x] Gate допуска: автоматическая стратегия не переходит в Micro-Live без пройденного исторического
  отчёта; прохождение не считается доказательством будущей доходности.
- [x] CLI-инспекция CSV с fingerprint и gap report.
- [x] Trade OHLCV, Mark Price OHLC и funding events с независимыми fingerprints,
  quality flags и общим выровненным timeline.
- [x] Mark Price trigger для защиты, отдельные maker/taker assumptions, forced-flatten
  и явный `production_equivalent=false` при неполных рядах.
- [x] UI/CLI запуска конкретного BackTest, JSON/CSV экспорт и воспроизводимый rerun
  по manifest с обязательной проверкой fingerprint.
- [x] Walk-forward folds, predefined stress suite и явная one-parameter sensitivity
  собираются в одном CLI manifest со списком всех проверенных parameter fingerprints.
- [x] Exact eligibility binding включает symbol/timeframe, версию Workbench, версию и
  параметры стратегии, Trade/Mark/funding dataset fingerprint, maker/taker fee, slippage,
  execution mode, price trigger и fingerprint реальных InstrumentRules.
- [x] Production eligibility требует Mark Price и непустой funding history; research-only
  отчёты и старые неполные записи не могут открыть Micro-Live.
- [x] Causal warm-up для OOS/walk-forward подавляет warm-up intents; equity curve и
  drawdown учитывают внутрисвечный mark-to-market adverse excursion.

Автоматический оптимизатор, подгоняющий стратегию под историю, не входит в первую
версию. Параметры сравниваются явно заданными экспериментами.

## E. Mainnet safety и execution

- [x] `SHADOW` блокирует все mutating requests до обращения к HTTP delegate.
- [x] `MICRO_LIVE` требует точной ручной фразы и короткоживущего arming ticket;
  full `LIVE` пока fail-closed и недоступен.
- [x] Allow-list мутаций: create/cancel и trading stop. Автоматические leverage/margin
  mutations удалены; gateway только проверяет isolated/1x.
- [x] Micro-Live: один symbol, max order notional/exposure/daily loss, rate interval,
  cooldown, isolated margin, leverage 1x и attached server-side stop.
- [x] Wallet/withdrawal/transfer, Spot, Options/USDC и неизвестные лишние permissions
  блокируют выдачу билета.
- [x] Перед каждым входом risk facts выводятся из свежего account-wide snapshot; поля
  notional/exposure/PnL/leverage/margin от стратегии отсутствуют.
- [x] Ticket связан с endpoint, key identity, symbol, strategy/version/parameters;
  после restart/expiry новые входы блокируются.
- [x] Kill switch запрещает входы и оставляет только cancel/reduce-only.
- [x] SQLite FSM execution-команд: planned/requested/acknowledged/confirmed.
- [x] Стабильный orderLinkId и запрет слепого retry после потерянного ответа.
- [x] Attached hard stop/TP, exchange-native trailing stop и проверка позиции.
- [x] Идемпотентное market reduceOnly emergency close.
- [x] Неподтверждённая защита переводит движок в EMERGENCY_STOP.
- [x] Ограничение первого среза лимитным входом документировано.
- [x] Подключить Mainnet coordinator к UI workflow Check → Arm → Run через один runtime.
- [x] Подтверждать Mainnet-команды по свежему private WS и использовать REST fallback.
- [x] Проверить Mainnet partial fills и повторную защиту исполненного количества.
- [x] Подключить Stop/Pause/Cancel entries/Cancel non-protective/Flatten/Emergency как
  UI-операции с preview, защитой protective orders и reconciliation.
- [ ] Подтверждать flat и persisted reconciliation после реального аварийного закрытия.
- [ ] Отдельный opt-in Micro-Live smoke: минимальная позиция → stop → trailing → close
  → подтверждённая нулевая позиция. Никогда не запускать автоматически.

## F. Стратегии

- [x] Базовый Strategy Protocol и versioned metadata/data requirements.
- [x] Полный набор намерений: Exit, UpdateProtection, CancelEntry и NoOp.
- [x] Реализация ManualProtectedTrade для полного ручного вертикального среза.
- [x] Registry и типизированная схема параметров; UI показывает регистрации fail-closed.
- [x] Утверждённые `user_algorithm_1` и `user_algorithm_2` зарегистрированы как
  `AUTOMATIC`, version `0.2.0`, с единой строгой валидацией параметров.
- [x] Формализовать Алгоритм 1: данные, вход, отмена, выход, sizing, stop/trailing,
  TP, повторный вход, cooldown, сбои, golden examples и counterexamples.
- [x] Реализовать Алгоритм 1 и golden/counterexample/restart/no-repaint tests.
- [x] Формализовать и реализовать Алгоритм 2 с frozen channel, strict confirmation,
  power filter, structural trailing и golden/counterexample/restart tests.
- [x] Versioned JSON-safe snapshots включают индикаторную историю; duplicate и
  out-of-order candles обрабатываются детерминированно.
- [x] `PENDING_UNKNOWN` сохраняется после неизвестного outcome/restart и блокирует
  следующую свечу до явного reconciliation со свежим exchange snapshot.
- [x] Fingerprint параметров хранится в state и входит в intent ID; изменение
  параметров у уже запущенной/восстановленной стратегии запрещено.
- [x] Partial fill создаёт только один cancel остатка; execution ID дедуплицируются,
  а более старый новый execution event отклоняется как out-of-order.
- [x] Алгоритм 2 отбрасывает неоднозначную свечу, одновременно коснувшуюся обеих зон.
- [x] Общий AutomaticStrategyRuntime для Replay, Mainnet Shadow и Mainnet execution,
  exact historical gate, state persistence hook и жёсткий запрет Demo.
- [x] Shadow recorder сохраняет реальные virtual intents, не имея write callback.
- [x] Execution intent sink повторно применяет RiskEngine и переводит Enter/Cancel/
  UpdateProtection/Exit в durable Bybit-команды; stop/TP используют MarkPrice.
- [x] `MainnetShadowSession` выполняет GET-only snapshot, безопасный warm-up и подаёт
  реальные закрытые свечи в общий AutomaticStrategyRuntime с журналом virtual intents.
- [x] Перед каждой закрытой свечой Mainnet Shadow получает новый GET-only snapshot;
  snapshot старше уже принятого отклоняется после reconnect как stale/out-of-order.
- [ ] Вывести отдельные desktop-кнопки запуска/остановки автоматического Shadow-сеанса;
  transport safety и программный data pump уже готовы.

## G. Desktop UI

- [x] Режим, engine state, Public/Private/REST health, balance и read-only connection.
- [x] Панели рынка, стратегии, риска, позиции, защиты и нижние журналы.
- [x] Ошибка отвечает на вопросы: что случилось, что сделано, что требуется.
- [x] Системное сохранение профилей API-ключей.
- [x] Настоящий OHLC/candlestick chart вместо линии close.
- [x] Entry/average/stop/TP/liquidation и risk zone на графике.
- [x] Перетаскивание только планируемого stop до входа с обязательным повторным Check.
- [x] Legacy Testnet Check → Arm → Run → Stop сохранён вне critical path.
- [x] Явная Pause strategy / запрет новых входов без отключения защиты позиции.
- [x] Отдельные Cancel entries, Cancel non-protective, Flatten и Emergency stop
  с preview последствий.
- [x] Дневной PnL из Bybit Closed PnL и отдельная UI-вкладка execution-команд из SQLite.

## H. Устойчивость и тестирование

- [x] Unit tests домена, risk, replay, persistence, Bybit mapping и execution.
- [x] Duplicate/out-of-order order/execution event handling.
- [x] Потерянный entry/protection/emergency REST response recovery.
- [x] GUI offscreen smoke и headless vertical smoke.
- [x] Property-based tests из ТЗ через Hypothesis.
- [x] FakeExchange: auth/rate-limit/clock-skew/symbol-halt/insufficient-margin faults.
- [x] Legacy Testnet recovery-тест сохранён; Mainnet restart всегда disarmed.
- [x] Disconnect-after-accept и lost-response тесты без слепого retry.
- [x] Опциональный soak test: 10 000 reconnect/dedup циклов и конкурентная SQLite
  запись; найденный lock timeout исправлен 30-секундным busy policy.

## I. Mainnet readiness и поставка

- [x] Именованный профиль `BotW-Mainnet` только в Windows Credential Manager.
- [x] UI показывает endpoint, scope аккаунта, Read/Write, IP binding, срок и права.
- [x] Fail-closed arming: permissions, caps, freshness/reconciliation boundary и
  отсутствие лишних торговых/wallet permissions.
- [x] Жёсткий cap первой Micro-Live сделки и отсутствие автоматического re-arm.
- [x] Локальная Windows PyInstaller one-file сборка и запуск упакованного headless smoke.
- [ ] Повторить сборку и запуск в отдельной чистой Windows VM.
- [x] Runbook установки, backup/restore БД, recovery и key rotation.
- [x] Автотест отсутствия встроенных секретов и withdrawal endpoint.
- [ ] Отдельное явное решение пользователя перед любой реальной Micro-Live заявкой.

## Hard stops / требуется внешний ввод

Оставшиеся внешние hard stops:

1. Локальный ввод `BotW-Mainnet` и подтверждение GET-only connection test.
2. Отдельная чистая Windows VM для независимой проверки установки/сборки.
3. Отдельное решение пользователя перед первым Micro-Live smoke. Он никогда не
   запускается автоматически; текущая работа останавливается на этом hard stop.

Диалог расширения риска стопом остаётся незакрытым намеренно: существующий UI вообще
не разрешает расширять подтверждённый stop, что безопаснее. Добавлять такое разрешение
нужно только вместе с согласованной продуктовой политикой и повторным risk approval.
