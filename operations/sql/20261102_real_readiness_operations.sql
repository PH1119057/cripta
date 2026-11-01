-- Owner decision 2026-11-02 / Issue #343:
-- durable technical REAL readiness operations. This table has no trading effect.
BEGIN;
SET LOCAL lock_timeout='3s';
SET LOCAL statement_timeout='20s';

CREATE TABLE IF NOT EXISTS control.real_readiness_operations(
    request_id TEXT PRIMARY KEY
      CHECK(request_id ~ '^[A-Za-z0-9_-]{8,128}$'),
    requested_by TEXT NOT NULL,
    release_commit TEXT NOT NULL
      CHECK(release_commit ~ '^[0-9a-f]{40}$'),
    scope_key TEXT NOT NULL,
    scope_json JSONB NOT NULL,
    scope_fingerprint TEXT NOT NULL
      CHECK(scope_fingerprint ~ '^[0-9a-f]{64}$'),
    state TEXT NOT NULL CHECK(state IN (
      'REQUESTED','CHECKING','STARTING_SERVICES','RECONCILING','VALIDATING',
      'READY_FOR_OWNER_ARM','BLOCKED'
    )),
    phase TEXT NOT NULL,
    progress_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    blocker_code TEXT,
    blocker_detail TEXT,
    retry_eligible BOOLEAN NOT NULL DEFAULT false,
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL,
    completed_at TIMESTAMPTZ,
    CHECK (
      (state IN ('READY_FOR_OWNER_ARM','BLOCKED') AND completed_at IS NOT NULL)
      OR
      (state NOT IN ('READY_FOR_OWNER_ARM','BLOCKED') AND completed_at IS NULL)
    ),
    CHECK (
      (state='BLOCKED' AND blocker_code IS NOT NULL)
      OR
      (state<>'BLOCKED' AND blocker_code IS NULL)
    )
);

CREATE UNIQUE INDEX IF NOT EXISTS real_readiness_one_active_per_scope
ON control.real_readiness_operations(scope_key)
WHERE state IN (
  'REQUESTED','CHECKING','STARTING_SERVICES','RECONCILING','VALIDATING'
);

CREATE INDEX IF NOT EXISTS real_readiness_scope_time
ON control.real_readiness_operations(scope_key,created_at DESC,request_id);

REVOKE ALL ON control.real_readiness_operations FROM PUBLIC;
GRANT SELECT,INSERT,UPDATE ON control.real_readiness_operations TO cripta;

COMMIT;
