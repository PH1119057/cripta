# Issue #343 — Generic REAL Gate Readiness / preparation contract (PROPOSAL)

Status: **DESIGN PROPOSAL, NOT CANON, NOT IMPLEMENTED, NOT DEPLOYED**.
Source base: `PH1119057/cripta:main@ba0df328c4fa52fa211af5e3ec22688476b1ae1f`.
Owner directive: 2026-11-02, one operator action should perform technical service recovery, report user-friendly durable progress, then explicitly authorized scoped OWNER ARM (never silently on deploy or restart).

This is a branch-local design checkpoint. The normative home of the accepted contract must be established via `DOCUMENTATION_INDEX §17`, `DEVELOPMENT_RELEASE §46.1`, ARCH ownership, MAP §1.4, SECURITY and GLOSSARY **before executable work**. This file must not be mistaken for a second architecture authority.

## BEFORE / AFTER ownership

| Capability | BEFORE (current) | AFTER (proposed) |
|---|---|---|
| Browser owner intent | Dashboard posts P90 arm immediately | Dashboard submits authenticated exact scope/release preparation request, displays durable progress; owner authorization scoped to the same request |
| Service liveness checks | P90 prearm evidence raises on first inactive unit | Generic readiness planner inventories every required account-route unit, records all unit statuses |
| OS start | Manual operator / deployment actor | Minimal privileged **unit-start broker** starts only pre-registered exact units selected from proven account-route; no arbitrary unit names or shell |
| Business readiness | Existing Strategy/Entry/Exit/AccountState owners | **Unchanged**: existing owners prove COMPLETE signed state, reconciliation, protection/Exit, release, consumer behavior |
| Final authorization | Existing owner ARM transaction | **Unchanged**: explicit authenticated owner scope + fresh atomic final gate; no broker permission to write arms/gate |
| Close/disarm | Existing owner DISARM path | **Unchanged** and reachable even if preparation fails |

No replacement of Entry, Exit, Lifecycle Supervisor or account bindings. The new broker does *not* own their functionality. For any unreviewed transfer of capability, HARD_STOP pending separate owner decision.

## Service registry and operations

Required-unit set MUST derive from approved immutable release/TradingAccount/AccountConnector/Strategy binding. The legacy P90 nine-unit tuple may be used for migration parity tests, not as a generic authority. Each registry entry has an exact unit, role, account scope, expected release identity, dependencies, start-eligibility and whether an active service may be restarted (default: **never**).

Only allowable auto-remediation is start of a verified `inactive` unit with no unknown in-flight operation; any `failed`, `activating`, `deactivating`, masked, ambiguous, wrong-release, permission-error or Exit continuity risk is `BLOCKED` until classified. Never automatically stop, restart, daemon-reload, change unit files, or clear lifecycle faults. Service `active` is not `READY`; independently verify fresh heartbeat/behavior.

Broker contract: fixed root-owned reviewed executable and registry; only `systemctl start` on immutable allowlisted exact units, no arguments supplied by client reaching shell, no command interpolation, no DB trades, no Exchange credentials, no gate/arm mutation. A separate unprivileged `cripta` coordinator owns operation status and verification, not OS control. Exact owner/group/modes, read/create/write/rename/lock paths, actual service User, polkit/sudo/service delegation and systemd sandbox MUST be proven from server before source implementation under DEV §§5.1,19.1–19.4. Do not grant general sudo to Dashboard or the runtime actor.

## Durable operation state machine

`REQUESTED -> CHECKING -> STARTING_SERVICES -> RECONCILING -> VALIDATING -> READY_FOR_OWNER_ARM`.
Every step may instead end in `BLOCKED(reason_code, scope, evidence_ref, occurred_at, retry_eligible)`.
Separate explicit owner authorization may then produce `ARMING -> ARMED`, with independent `DISARMING -> DISARMED`.
A browser timeout does not imply failure or success; GET existing request by id before any retry. Record exact operator, request_id, target immutable release, Strategy/account/connector, unit snapshots, attempted start outcomes, lock owner, timestamps, deadline and terminal result. Bound both start count and total duration; no unbounded loops. A duplicate request_id is idempotent; conflicting payload under reused request_id is rejected. Only one active preparation per overlapping account/scope.

If any gate is not proven, leave global mainnet NEW-Entry gate closed; protect and reconcile existing positions independently.

## Dashboard UX contract

One visible top-level owner action with steps:
1. "Проверяем службы" with `n/M` checked.
2. "Запускаем недостающие службы" with exact readable names.
3. "Проверяем аккаунты, позиции и Exit".
4. "Готово к подтверждению владельцем" or "Заблокировано: <plain-language reason>".

Pending submit disabled while active; persistent state recoverable after refresh and cross-session. Terminal BLOCKED shows concise reason **in place**, more technical evidence available via details, and only a safe retry when explicitly eligible. Genuine DISARM remains visible during degraded armed state and never depends on readiness checks.

Never imply successful service start until OS state + behavior proof confirms it. Never imply trading permission until current exact authorization commit succeeds.

## Test/release gates

- Unit tests: scope-to-unit binding, deterministic dependency order, invalid/unknown unit fail-closed, absent/stale/wrong-release identity, duplicate/request conflict, concurrent requests, bounded retries, broker subprocess arguments (no arbitrary shell), simulated incomplete/timeout cases.
- Integration: disposable DB for persistent transitions and two TradingAccounts, cancellation/disconnect/browser refresh, exact owner-authenticated versus unauthenticated requests, blocked then repaired routes.
- Service: inactive->started->READY; failed->BLOCKED; active-but-stale->BLOCKED; broken permissions -> BLOCKED without privilege escalation; no service restart while active or positions ambiguous.
- Trading: account inventory missing/ambiguous, critical faults, unsafe Exit, protection not confirmed, stale observer/consumer; NEW Entry remains gate-closed. DISARM degraded. No default Strategy activation or automatic release arm.
- Release: static checks, Python compile/ruff/pytest, frontend syntax/UI browser tests, disposable PostgreSQL CI, cross-route exact-snapshot review, all required GitHub checks green, reviewed source + one verified release, actual production read-only liveness AND behavior evidence. Owner ARM explicitly remains OFF after deployment.

## Verified issue evidence

At baseline: `cripta-lifecycle-account@ta-853ac5fcd58185697396d33cd57459fa.service` and `cripta-account-position@ta-853ac5fcd58185697396d33cd57459fa.service` were inactive; both configured `User=cripta Group=cripta`. Their `ReadWritePaths` were account-scoped under `/var/lib/cripta/`. SentinelX direct path stat returned Unix EACCES; not a permission PASS for either service actor. `systemctl show` is a read-only check, not authorization to start them.

Pending: full actor/path matrix and broker policy; document index/architecture updates; implementation/tests/CI/release. No Exchange mutation approved by this proposal.
