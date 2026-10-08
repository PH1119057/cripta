-- R1 pending Entry cancel intent: persist Strategy cause before Exchange cancel.
-- Operational audit only; does not alter Strategy/Entry/Exit trading policy.
BEGIN;

CREATE TABLE IF NOT EXISTS runtime.entry_cancel_intents (
    order_id text NOT NULL,
    command_id text NOT NULL,
    reason text NOT NULL,
    requested_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY(order_id, command_id),
    CHECK (order_id <> ''),
    CHECK (command_id <> ''),
    CHECK (reason <> '')
);

REVOKE ALL ON runtime.entry_cancel_intents FROM PUBLIC;
GRANT SELECT, INSERT ON runtime.entry_cancel_intents TO cripta;

COMMIT;
