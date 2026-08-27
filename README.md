# Bybit Strategy Workbench

Локальное fail-closed приложение для проверки торговых стратегий. На текущем этапе
реализованы доменные модели, состояния приложения, профили окружений, SQLite-журнал,
детерминированная фальшивая биржа, независимый Risk Gate, защитные стоп-политики и
desktop-интерфейс оператора, Mainnet-first safety gateway и единый Mainnet execution
coordinator. Реальное исполнение по умолчанию невозможно: каждый запуск начинается
в `SHADOW`/disarmed.

Risk Gate блокирует вход при устаревших данных, превышении дневного лимита, cooldown,
недопустимом символе/направлении/часе, отсутствии требуемого буфера до ликвидации и
других нарушениях. Объём округляется вниз по `qtyStep` и не превышает риск-бюджет с
учётом настроенных комиссии и проскальзывания.

Replay принимает только закрытые свечи в строгом хронологическом порядке. Рыночный
вход исполняется на следующей доступной цене, лимитный — только после пересечения.
Поддерживаются partial fills, stop/take-profit, gap через stop, консервативный исход
неоднозначной свечи, комиссии, funding, идемпотентные аварийные действия и JSON-safe
snapshot/restore открытой позиции.

SQLite работает в WAL-режиме и обновляется версионированными миграциями. Отдельные
таблицы сохраняют настройки, запуски стратегий, решения, intents, risk checks, заявки
и их историю, executions, позиции, стопы, engine snapshots и reconciliation. Для
одного intent можно восстановить всю цепочку решения. Поля ключей, секретов, подписей
и authorization-заголовков редактируются до сериализации.

Bybit V5 read-only слой поддерживает instrument metadata, historical candles, UNIFIED
wallet, one-way linear position и open orders через REST, а также kline/ticker и
order/execution/position/wallet через WebSocket. REST является начальным snapshot,
WS — потоком обновлений. Встроены freshness health, heartbeat 20 секунд, reconnect с
exponential backoff и jitter, восстановление подписок и обязательный reconciliation
после private disconnect. Торговые endpoints в этом слое отсутствуют.

Desktop-интерфейс показывает режим, состояние движка, freshness Public WS / Private
WS / REST, балансы, цену, позицию, риск-план, planned/requested/confirmed защиту и
отдельные журналы решений, risk events и системных событий. Сетевой read-only lifecycle
работает вне Qt-потока; REST-синхронизация предшествует WebSocket-подпискам, а ошибки
автоматически переводятся в безопасное для входов состояние.

Mainnet API-профиль `BotW-Mainnet` сохраняется только через системный keyring
(Windows Credential Manager). Секрет не подставляется обратно в UI, не сохраняется в
SQLite и редактируется из сообщений об ошибках. Endpoint выбирается явно между
`api.bybit.com`, `api.bybit.kz` или ручным HTTPS override; скрытого fallback нет.

Mainnet имеет три независимых safety mode: `SHADOW`, `MICRO_LIVE`, `LIVE`. В `SHADOW`
любая мутация блокируется до HTTP delegate, а стратегия пишет только virtual intents.
`MICRO_LIVE` требует точной ручной фразы и короткоживущего билета, связанного с
endpoint, профилем ключа, единственным символом, стратегией, её версией и fingerprint
параметров. После перезапуска или истечения билета новые входы невозможны. Full
`LIVE` пока намеренно не активируется.

Перед каждой мутацией gateway получает свежий согласованный снимок и самостоятельно
вычисляет notional, gross exposure и дневной убыток. Для входа обязательны полный
account-wide снимок, UTA 2.0, isolated margin, leverage 1x, limit order и attached
Mark Price stop. Стратегия не может передать gateway собственные значения риска.

Каждое действие до сетевого вызова попадает в SQLite как идемпотентная execution-команда
со стадиями `planned → requested → acknowledged → confirmed`. Потерянный REST-ответ не
приводит к слепому повтору: заявка сверяется по стабильному `orderLinkId`. Биржевой
hard stop прикрепляется уже к лимитному входу, а после исполнения отдельно проверяется
по снимку позиции. Если Bybit не подтвердил защиту, движок переходит в
`EMERGENCY_STOP` и запрашивает market `reduceOnly` закрытие. Перенос стопа в сторону
увеличения риска запрещён.

Desktop workflow Mainnet проходит через единый runtime: локальный Risk Check, GET-only
account-wide preflight, точный `ARM MICRO_LIVE`, отдельное подтверждение Run и durable
coordinator. Команды сверяются по свежему Private WS с REST fallback; partial fills
повторно проверяются и защищаются. Exact historical eligibility привязан к
`symbol/timeframe`, коду, параметрам, данным, комиссиям, execution model и реальным
`InstrumentRules`. Для первого operator-timed smoke подключена manual protected strategy;
автоматические стратегии по-прежнему требуют exact historical gate. Внешний live switch
по умолчанию выключен, поэтому один лишь запуск приложения не включает торговые POST.

На этом срезе обычный вход разрешён только лимитной заявкой. Bybit не позволяет
одновременно использовать параметры собственного market-slippage cap и attached TP/SL,
поэтому market-вход не включён до реализации безопасной IOC-limit альтернативы.
Market используется только для аварийного `reduceOnly` закрытия.

## Быстрая проверка без зависимостей

```powershell
$env:PYTHONPATH = "src"
python -m bybit_workbench --headless
python -m unittest discover -s tests -v
```

## Установка окружения разработки

После установки `uv`:

```powershell
uv sync --extra dev
uv run pytest
uv run bybit-workbench
```

Либо обычным `pip`:

```powershell
python -m pip install -e .
python -m bybit_workbench
```

Для Mainnet Shadow задайте профиль окружения, запустите приложение и сохраните ключи
кнопкой «Профиль API-ключей». Реальные секреты не должны храниться в `.env`:

```powershell
$env:BYBIT_WORKBENCH_PROFILE = "live"
$env:BYBIT_WORKBENCH_REST_URL = "https://api.bybit.kz" # либо api.bybit.com
python -m bybit_workbench
```

Первое подключение выполняет только GET server time, `/v5/user/query-api`, balance,
positions и open orders. UI показывает endpoint, master/subaccount, Read/Write, IP,
срок и фактические permissions. Wallet/withdrawal/transfer права блокируют arming;
Spot/Options/USDC и неизвестные лишние права также блокируют Micro-Live ticket.

Текущий статус требований и оставшиеся работы ведутся в
[`IMPLEMENTATION_CHECKLIST.md`](IMPLEMENTATION_CHECKLIST.md). Historical-контур уже
включает строгий CSV-импорт, fingerprint данных, chronological split, walk-forward,
универсальный Strategy runner через общие RiskEngine/ReplayEngine,
Trade/Mark/funding fingerprints, отдельные maker/taker комиссии, mark-to-market
intrabar drawdown, causal warm-up и сохраняемый exact eligibility report. Решение на
закрытии свечи не может исполниться раньше следующей свечи. Отчёт не трактуется как
доказательство будущей доходности. Обе автоматические стратегии версии `0.2.0`
реализованы; Mainnet execution требует точного совпадения strategy/version/parameters
с пройденным exact historical gate. BackTest никогда сам не включает execution.
После прохода 5 неизвестный outcome сохраняется как `PENDING_UNKNOWN` до reconciliation,
parameter fingerprint входит в persisted state/intent ID, а Mainnet Shadow получает
новый GET-only snapshot перед каждой закрытой свечой и отклоняет stale snapshot после
reconnect. В проходе 8 manual protected Micro-Live provider подключён только через
короткоживущий exact-plan ticket и остаётся заблокирован внешним live switch до явного
подтверждения.

Проверить CSV без запуска GUI можно командой:

```powershell
python -m bybit_workbench --inspect-history candles.csv --symbol BTCUSDT --timeframe 1
```

Research-only BackTest с JSON/CSV экспортом:

```powershell
$rules = Get-Content .\rules.json -Raw
python -m bybit_workbench --backtest trade.csv --mark-history mark.csv `
  --funding-history funding.csv --symbol BTCUSDT --timeframe 60 `
  --strategy user_algorithm_1 --instrument-rules $rules `
  --report-json report.json --trades-csv trades.csv --strict-market-data

python -m bybit_workbench --rerun-report report.json
```

К базовому запуску можно добавить `--walk-forward-training-bars 500
--walk-forward-test-bars 200`, `--stress-suite` и явную sensitivity-проверку вида
`--sensitivity-parameter entry_lookback --sensitivity-values '[45,55,65]'`.

`--instrument-rules` обязателен для BackTest и должен содержать точные правила
выбранного инструмента. Без Mark Price или непустого funding history тест остаётся исследовательским и
не является production-equivalent. Для сохранения production eligibility добавляется
`--eligibility`; в GUI эти правила и maker/taker fee берутся из свежего read-only
snapshot. Тот же запуск доступен во вкладке `BackTest` desktop UI.

Инструкция установки, сборки, backup/recovery, ротации ключей и отдельного Micro-Live
smoke находится в [`RUNBOOK.md`](RUNBOOK.md). Smoke никогда не запускается автоматически.

Статус Windows release gate и его границы приведены в [`PASS6_REPORT.md`](PASS6_REPORT.md).

## Pass 7 — Mainnet GET-only acceptance

Pass 7 adds a separate diagnostic CLI path that owns no write transport. For a
Kazakhstan account use the regional Mainnet endpoint and keep the live switch off:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\accept_mainnet\_windows.ps1 `
  -Symbol UNIUSDT `
  -Endpoint https://api.bybit.kz
```

The generated `var\mainnet_acceptance.json` is safe to review/share: it contains no
API key value, secret, or bound IP values. It reports master/subaccount status,
UTA/margin mode, one-way/hedge mode, permissions, positions/orders, maker/taker fees,
and exact instrument rules. `micro_live_ready=false` means configuration blockers were
found; the script does not try to fix them and never sends a trading mutation.

## Pass 8 — editable percentage risk and exact Micro-Live plan binding

The desktop risk field is operator-editable and defaults to `1.00%` of synchronized
equity. The optional absolute USDT cap defaults to `0` (disabled). A checked Micro-Live
entry is sealed into a short-lived in-memory ticket; the write gateway accepts only the
exact checked symbol, orderLinkId, side, quantity, limit price, stop and take-profit.
Editing market or risk inputs invalidates the plan and returns execution to SHADOW.
