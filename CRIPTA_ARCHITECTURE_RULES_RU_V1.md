# CRIPTA — архитектурные правила проекта

Версия: 1.0 · 2026-09-05  
Назначение: роли слоёв, live-safety, research-contract и зафиксированные архитектурные долги.  
Процесс patch/install/Git вынесен в `CRIPTA_ASSISTANT_WORK_RULES_RU_V1.md`.

## 1. Главный принцип
Каждый слой имеет одного владельца ответственности. Слой не должен незаметно выполнять работу соседнего слоя. Если границы меняются — сначала обновляется архитектурный контракт, затем код.

## 2. Market Data / Connectivity / Exchange Sync
**Владеет:** WS/REST, clock drift, reconnect/watchdog, positions/orders/fills/balance/leverage/margin mode, reconciliation, свежестью exchange state.  
**Не владеет:** торговым решением.  
**Правило:** свежий Bybit = live truth. Неизвестное обязательное состояние => `BLOCKED/WAITING/ERROR`, без silent defaults.

## 3. MAYAK
**Владеет:** независимым наблюдением рынка, режима, денег/объёма, OI, tape/orderbook/context, при необходимости внешнего контекста.  
**Не владеет:** Entry, stop, close, Risk.  
MAYAK сообщает состояние/предупреждение, но не торгует и не становится скрытой стратегией.

## 4. Entry
**Владеет:** причинным решением «есть ли допустимый вход сейчас», causal geometry/context, состояниями `WARMUP/WAITING/WATCH/APPROACH/SIGNAL/COOLDOWN/NO CALIBRATION/ERROR/BLOCKED`.  
**Не владеет:** сопровождением позиции после confirmed fill, BE/trailing/close, Exit/Risk.  
Только causal online data, без look-ahead/future-derived artifacts. После restart/reconnect обязательные causal features re-warm/restored.

## 5. Initial protection: Entry -> Execution
Текущий контракт V36.1.11: `entry_v1_core` задаёт immutable initial protection: SL 1.00%, TP 3.00%, `LastPrice`, `Full`. Execution ставит server-side protection вместе с Entry order и после fill может только re-anchor те же параметры к actual avg fill.

## 6. Execution
**Владеет:** live readiness, биржевыми mutations, actual fill, durable handoff, order/client/protection IDs, reconciliation и server-side protection.  
**Не владеет:** повторным вычислением Entry logic.

Durable handoff минимум: symbol, side, actual avg fill, qty, fill time, initial protection, exchange/client IDs, protection IDs, ownership/trade IDs.

## 7. Exit
После confirmed fill владеет сопровождением позиции: protection transitions, BE, trailing, close, restart recovery.  
Не ретюнит Entry и не переопределяет frozen Entry geometry.  
V36.1.11: structural early-loss auto-close отсутствует — `DISABLED_NOT_PROVEN_NO_CLOSE_IMPLEMENTATION`.

## 8. Risk
Владеет допустимым денежным риском. Различать price move %, R, equity %, notional, margin, leverage.  
`monetary risk = position size × stop distance + costs`.  
Leverage меняет margin, но не должен сжимать structural stop, увеличивать допустимый убыток или менять Entry/Exit levels.

## 9. Economic break-even
Production BE учитывает по возможности entry/closing fees, slippage, funding и валидный Bybit `breakEvenPrice`. Теоретический `0.00%` в research не равен гарантированному net-zero.

## 10. Position Supervisor
**Владеет:** карточкой позиции, causal context, наблюдением structure/flow/OI/orderbook, diagnostic/advisory state.  
**Не владеет:** Entry, close, stop, trailing, Risk.  
В V36.1.11 остаётся observation/context layer.

## 11. Restart / reconnect / clock safety
После restart нельзя считать систему warm. Обязательны clock check, reconnect, reconciliation actual positions/orders/fills/qty/avg fill/stops/IDs и re-warm causal features. Нельзя открыть вторую позицию потому, что local memory говорит `flat`. Server-side stop, cancel-pending, reduce-only close, reconciliation и emergency kill не зависят от scanner/research logic.

## 12. Live fail-closed
Если обязательное по утверждённому контракту состояние missing/stale/invalid — trading mutation запрещена. Observation и trading permission — разные вещи: наблюдение может продолжаться при `BLOCKED` для торговли.

## 13. SHADOW -> MICRO_LIVE -> LIVE
Новая автоматизация проходит: research equivalence -> SHADOW -> live feature/signal equivalence -> MICRO_LIVE -> LIVE. Каждый переход — отдельное решение владельца. После restart Entry по умолчанию disarmed, если явно не утверждено обратное.

## 14. Research != Production
Research фиксирует provenance и может работать с historical datasets. Production содержит только causal online logic и не зависит от multi-GB history/future-derived artifacts.

## 15. Holdout / OOS
Discovery отделяется от confirmation. Просмотренный holdout больше не чистый для retuning. Новая настройка требует нового unseen asset/time/sample. Для Entry filter считать не только failures saved, но и lost good entries, retention, signals/day, continuation, runners, cross-asset stability.

## 16. Simple benchmark first
Сложная Entry/Exit/Risk логика должна заметно превосходить заранее заданный простой benchmark с учётом fees/slippage, PF, drawdown, stability, concentration, trade count и operational complexity.

## 17. Data / provenance
Frozen/offline research не должен молча скачивать другой dataset или чинить/удалять плохие данные. Missing/corrupt/schema/timestamp/duplicates фиксируются или fail-closed. Serious result хранит version, period, symbols, fingerprints, params, dev/holdout label, signal count, completeness, software/logic version. CSV/JSON = machine truth; Markdown = presentation.

## 18. Signal replay != portfolio backtest
Независимые 72h paths измеряют signal quality, а не portfolio PnL. Portfolio test обязан моделировать chronology, one active position/symbol если принято, finite capital/margin, simultaneous positions и deterministic conflicts.

## 19. UI / Audit
UI — observation/control, не скрытая стратегия. Cosmetic change не меняет trading logic. Параметры UI, влияющие на Entry/Exit/Risk, обязаны иметь runtime contract и audit. Live trail должен сохранять решение, causal inputs, order/fill/protection/Exit и итог.

## 20. Hard stop при конфликте архитектуры и кода
Если код расходится с действующей концепцией, разработка останавливается. Либо код исправляется под концепцию, либо владелец сначала меняет концепцию. Нельзя тихо менять смысл слоя внутри технического patch.

---

# Текущий stable checkpoint

- Production: **V36.1.11**.
- GitHub `main` = server `source_checkout`: `22f1ed07ec34a4713f23d4d196765ded545ec610`.
- Tracked worktree: clean.
- 21 ожидаемый local untracked research/test artifact сохранён вне Git.
- Ключевые V36 source/live files: MATCH.
- `postgresql`, `private-runtime`, `entry-shadow-scanner`, `position-supervisor`, `exit-runtime`: active на последней проверке.
- `ENTRY_GATE=DISARMED`.
- На последней readiness-проверке: positions=0, active orders=0, active commands=0.
- Exit runtime: `RUNNING`.
- Early-loss automation: disabled/not proven/no close implementation.

Этот checkpoint подтверждает техническую стабильность текущего поведения, но не означает завершённый полный аудит всей логики.

# Что сейчас не так / требует отдельного решения

## A. Устаревший source-of-truth contract
Старое правило «`C:\cripta` — source of truth» больше неверно. Теперь авторитетны server `source_checkout` + GitHub `main`.

## B. Старые правила смешивали Windows и Linux production deployment
Windows остаётся отдельной целевой средой, но production server использует собственный rail `/usr/local/sbin/cripta-apply-incoming`. Эти два installer-contract нельзя смешивать.

## C. Observation universe и live-trading eligibility явно не разделены в архитектурном документе
Фактически scanner наблюдает 20 enabled symbols. На последней проверке 8 не имели индивидуальной OI calibration: `APT, ARB, BCH, DOT, HBAR, INJ, OP, TRX`. Был зафиксирован CORE signal по INJ при `oi=uncalibrated`.

Это **не разрешение на изменение кода**. Нужно отдельное решение владельца: отсутствие calibration допустимо только для observation или также для live Entry, и где именно находится trading-eligibility boundary. До решения текущее поведение не менять.

## D. Полный логический аудит после stable checkpoint ещё не выполнен
Сейчас подтверждены техническая стабильность, source/live equivalence и Git sync. Отдельно ещё предстоит аудит соответствия всех Entry/Exit/Risk/MAYAK/restart contracts архитектуре. Его нельзя начинать внутри текущего стабилизационного цикла без отдельной команды.
