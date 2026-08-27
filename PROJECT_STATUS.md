# Bybit Strategy Workbench — статус проекта

## Целевая установка

- Windows 10 x64.
- Корневая папка: `C:\cripta`.
- Python 3.13.12 x64.
- Mainnet-first; каждый запуск начинается только в `SHADOW`/disarmed.
- API-профиль остаётся в Windows Credential Manager и не входит в архив.
- Специальный основной аккаунт без субаккаунта; бот работает только с Unified Trading.

## Проходы

1. **Воспроизводимый baseline** — завершён 13 августа 2026.
2. **Mainnet safety и короткоживущий arming ticket** — завершён 13 августа 2026.
3. **Mainnet execution coordinator и UI/runtime integration** — завершён 14 августа 2026.
4. **BackTest и exact historical eligibility** — завершён 14 августа 2026.
5. **Стратегии, SHADOW, restart/reconnect/unknown/partial-fill** — завершён 14 августа 2026.
6. **Чистая Windows-сборка** — завершён 14 августа 2026; one-file AMD64 EXE и packaged smoke подтверждены на целевой Windows.
7. **GET-only проверка реального аккаунта** — завершена 14 августа 2026; реальный acceptance дал `micro_live_ready=true` без сетевых мутаций.
8. **Micro-Live risk-bound arming** — подготовка исходников: редактируемый процент риска и точная привязка плана; реальный POST ещё не разрешён.

Первый реальный Micro-Live POST требует отдельного явного подтверждения владельца
аккаунта после авторитетной Windows-проверки прохода 8.

## Текущая граница безопасности

`SHADOW` блокирует любую мутацию до транспорта. Micro-Live допускается только по
неподделываемому короткоживущему билету, связанному с endpoint, профилем ключа,
единственным символом, версией стратегии и fingerprint параметров. Перед каждой
записью gateway заново получает свежий account-wide снимок и сам вычисляет notional,
общую экспозицию и дневной убыток. Данные риска от стратегии не принимаются.

Mainnet coordinator подключён к desktop workflow через единственный runtime. Команды
подтверждаются по свежему Private WS с REST fallback; потерянный или некорректный
ответ никогда не вызывает слепой повтор. Partial fill проверяется по позиции, hard
stop подтверждается на бирже, аварийное закрытие допускает только cancel и market
reduce-only.

Для входа обязательны UTA 2.0, isolated margin, one-way `positionIdx=0`, leverage 1x,
полный account-wide снимок позиций/ордеров, лимитная заявка и attached Mark Price stop.
Права Spot, Options/USDC, Wallet и неизвестные лишние права блокируют билет. Изменение
margin mode и leverage через бота отключено.

Historical eligibility связан с точным `symbol/timeframe`, версией кода, версией и
параметрами стратегии, Trade/Mark/funding dataset fingerprint, комиссиями, slippage,
execution model и реальными `InstrumentRules`. Production eligibility требует Mark
Price и непустую funding history.

В проходе 5 обе автоматические стратегии переведены на version `0.2.0` и state v2.
Неизвестный результат entry сохраняется как `PENDING_UNKNOWN`, переживает restart и
блокирует обработку следующей свечи до reconciliation. Fingerprint параметров хранится
в persisted state и входит в intent ID; hot-change параметров запрещён. Execution ID
дедуплицируются, более старые события отклоняются, а partial fill вызывает только один
cancel незаполненного остатка. Алгоритм 2 отбрасывает свечу, одновременно коснувшуюся
обеих зон.

Mainnet Shadow перед каждой закрытой свечой делает новый GET-only snapshot. Snapshot,
который после reconnect оказался старше уже принятого, отклоняется. Виртуальные entry
и cancel в SHADOW меняют только локальное состояние и журнал — write callback к Bybit
не подключён.

Текущий Workbench имеет `0.8.5`, автоматические стратегии остаются `0.2.0`. Exact
eligibility для предыдущей версии кода намеренно не подходит текущему release. Для
автоматической Micro-Live сделки нужен новый exact BackTest/eligibility именно текущей
версии; первый operator-timed smoke использует отдельную manual protected strategy.

Full `LIVE` намеренно не активируется. Проход 8 подключает только risk-bound manual
Micro-Live arming path; внешний `BYBIT_WORKBENCH_ALLOW_LIVE_TRADING` по умолчанию
остаётся выключен, а реальный POST требует отдельного подтверждения владельца.

Результаты находятся в `PASS1_REPORT.md` … `PASS8_REPORT.md` и `START_HERE_RU.md`.

## Pass 7 — Mainnet GET-only acceptance

Workbench `0.7.0`. Добавлен отдельный GET-only acceptance runner без зависимости от
write transport. Он формирует редактированный отчёт по endpoint, master/subaccount,
API permissions, IP-binding count/expiry, UTA 2.0, margin/position mode, leverage,
контрактным позициям и заявкам, fee rates и instrument rules. Реальные API key/secret
и значения bound IP в отчёт не записываются. `parentUid="0"` и epoch-expiry sentinel
Bybit нормализуются корректно. Торговые POST на проходе 7 отсутствуют.

## Pass 8 — Micro-Live risk-bound arming preparation

Workbench `0.8.5`. Risk per trade is now an editable percentage (default `1.00%`) with
an optional absolute USDT cap (`0` = disabled). The Micro-Live ticket seals the exact
normalized entry plan and the Mainnet gateway rejects any changed quantity, price,
stop, take-profit, side or order link id. Symbol/timeframe/strategy/risk edits invalidate
the checked plan. The first smoke can use manual protected timing; automated strategies
continue to require exact historical eligibility. External Mainnet live switch remains
off by default.


Pass 8 r5 adds a persistent searchable MRU symbol selector. Successful symbols are
stored locally in `var/symbol_history.json` (max 50, no credentials), while free-form
manual entry remains available.
