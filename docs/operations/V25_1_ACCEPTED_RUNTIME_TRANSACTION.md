# Accepted runtime transaction repair V25.1 — canonicalization

Canonical baseline before this sync: `ad506e186b2dce57edf327a2d5db1fd4398e765a`.

Accepted production sequence:
- V24.4 fixed the proven PostgreSQL ownership JOIN defect (`TEXT = NUMERIC`).
- V25.1 fixed the proven private-runtime long outer transaction.
- V25.1 closed the empty command-poll implicit transaction.
- V25.1 fixed the analogous opportunity-tracker read/write transaction boundary.

Real production acceptance evidence on 2026-09-03:
- before V25.1: `long_idle_xact_gt5s=1`, `aborted=0`, `waiters=0`;
- after V25.1: 15 consecutive health samples with `long_xact_gt5s=0`, `aborted=0`, `waiters=0`;
- `V24_OWNERSHIP_FIX_PRESERVED=PASS`;
- `ENTRY_GATE_FINAL=DISARMED`;
- `INSTALL_STATUS=PASS`;
- `V25_1_COMPLETE=PASS`.

Canonical sync V25.2 operational correction: pre-existing untracked files are not interpreted as source changes. The installer requires tracked worktree and index clean, snapshots every untracked file by path/size/SHA256, never stages or deletes them, and requires the exact snapshot to remain unchanged.

This commit does not change live production bytes, service state, Entry/Exit/Risk semantics, leverage, stake, TTL, structural hard stop, trailing/profit protection, Mayak/Dispatcher strategy logic, frozen research, or owner re-arm state.

## Canonical-sync packaging correction V25.3

V25.2 failed before canonical source mutation because an installer-only staged helper
was invoked under `cripta` while the runner staging tree is root-owned. V25.3 preserves
that security boundary: the staged helper runs as root; Git inspection of the canonical
checkout inside the helper is explicitly executed as `cripta`.

## Canonical-sync packaging correction V25.4

V25.3 failed before canonical source mutation while writing the temporary untracked
snapshot. V25.4 keeps that snapshot root-owned (mode 0600) and rollback only inspects
it after successful creation.

## Canonical-sync push transport correction V25.5

V25.4 created the expected canonical commit locally, then failed because push ran as
`cripta`. The established `github-cripta` alias and deploy key are root-owned and
authenticate successfully to `PH1119057/cripta`. V25.5 changes no credentials: commit
creation remains `cripta`; only the network push uses the existing root deploy-key route.
