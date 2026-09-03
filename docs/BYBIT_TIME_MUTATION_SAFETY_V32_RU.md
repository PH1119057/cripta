# BYBIT TIME / MUTATION SAFETY V32

## Граница изменения

V32 меняет только безопасность live execution transport/reconciliation.

Не меняются:

- Entry geometry/fingerprint/thresholds;
- Exit и Risk;
- структурный hard stop и leverage policy;
- stake;
- Mayak и Dispatcher;
- frozen research / holdout;
- scanner strategy logic;
- схема PostgreSQL;
- persistent incoming runner;
- owner re-arm state.

## Причина

V31/V31D2 показали, что системные часы Ubuntu синхронизированы, но live signed
REST имел `recvWindow=5000` при socket timeout 10 секунд. Для mutating POST это
создавало класс ошибки, когда результат запроса мог стать неизвестным локально,
а command worker мог записать `failed` до обязательной exchange reconciliation.

## Контракт V32

1. `recvWindow` остаётся 5000 ms. Он не расширяется.
2. Signed REST I/O timeout = 3.0 s.
3. Signed GET может повториться один раз только при явном `retCode=10002`.
4. Mutating POST автоматически не повторяется никогда.
5. Перед каждым POST выполняется midpoint clock check Bybit; absolute offset
   больше 500 ms блокирует мутацию fail-closed.
6. Timeout/URLError/нечитаемый ответ POST создают `EXCHANGE_MUTATION_BARRIER`:
   Entry disarm, best-effort executions/reconciliation, команда остаётся
   unresolved до restart reconciliation, процесс private runtime завершается.
7. Startup повторно подтягивает executions после cancel pending Entry.
8. Если exact bot Entry fill пережил restart без protection, серверный initial
   stop/target восстанавливаются от фактического exchange average fill.
9. Старые `running` non-Entry mutations после restart не проигрываются повторно.
10. Safety observer считает clock offset по midpoint RTT, отдельно от latency.

Entry после установки остаётся DISARMED. Re-arm — отдельное решение владельца.
