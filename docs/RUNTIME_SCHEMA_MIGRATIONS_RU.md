# Runtime schema: fail-closed migration boundary

Baseline: `a61bc0c170d66f1962f8f22fc22e901f47527338`.

## Contract

`cripta-private-runtime.service` must never run `CREATE`, `ALTER`, or `DROP` during
normal startup or reconnect. Startup first closes the new-Entry gate, then performs
a read-only schema/version validation. Missing or incompatible schema is `BLOCKED`;
no Entry worker is started.

Schema mutation belongs to the predeploy installer only. The installer runs the
versioned migration after the final overlay gate and after the tested GitHub
checkpoint exists. Migration uses an explicit short PostgreSQL `lock_timeout`, is
transactional, and fails closed. A failure leaves Entry disarmed and does not deploy
the new runtime.

The current migration registers the already-existing validated production schema as
`runtime-schema-2026-09-02-v1`. It does not retune strategy logic and does not alter
existing trading tables. Future schema changes must add a new explicit versioned
migration; they must not restore startup DDL to the live runtime.

The Entry shadow scanner uses PostgreSQL autocommit for the connection itself and
opens explicit transactions only around `persist_signals()`. This prevents an
implicit outer transaction from converting the persistence block into a savepoint
and leaving `idle in transaction` locks behind.

## Does not change

Entry geometry/thresholds, `m3_full_live_v1`, stake, leverage, Entry offset/TTL,
hard stop `-1.00% PRICE MOVE`, profit protection, trailing `0.30%`, Exit/Risk,
Mayak, Dispatcher, frozen research, or historical close semantics.
