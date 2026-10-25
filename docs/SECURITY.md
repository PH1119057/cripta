# CRIPTA — security baseline

**Версия:** 1.5
**Дата:** 2026-10-25
**Статус:** обязательный technical security baseline

Этот файл входит в base pre-read для любой содержательной работы.
Он содержит technical security invariants и не задаёт trading policy.

- Не добавлять API keys, secrets, private keys, cookies, authorization headers
  и иные credentials в repository, issue, screenshot, logs, docs или research output.
- Не публиковать private-key material и credential paths/names, если они не
  необходимы для безопасной эксплуатации и не одобрены владельцем.
- Credentials хранить только в approved secret stores с least privilege.
- Deploy-host GitHub write credential не является обязательным release
  prerequisite. Publisher и deploy roles разделяются.
- Read permission не означает trading mutation permission.
- Mainnet mutation разрешается только через current canonical gate + exact
  execution path + LIVE-arm readiness; отсутствие доказанного разрешения =
  fail-closed.
- Unknown fill/qty/position/protection, stale private state, потеря
  reconciliation, stale/unknown position mode или required state блокируют
  дальнейшую mutation.
- Critical lifecycle/operational fault должен иметь durable owner-notification
  delivery с retry и acknowledgement/escalation; UI-only не считается доставкой.
- Withdrawal/transfer privileges не выдаются trading runtime без отдельного
  owner-approved architecture decision.
- Filesystem privilege выдаётся exact effective actor только на exact required
  path/operation. Root, sudo и service sandbox не используются как замена
  корректному owner/group/mode/ACL contract.
- `ReadWritePaths=` systemd открывает sandbox path, но не выдаёт Unix write
  permission. Оба gates проверяются независимо до запуска workload.
- `chmod 777`, бесконтрольный recursive `chown/chmod` и запуск всего workflow
  от root запрещены как автоматический способ лечить permission failure.
- Рабочие/temp/backup/output каталоги создаются final writer'ом либо получают
  exact least-privilege owner/group/mode/ACL до первого write другого actor.
- Current non-secret server actor/path profile хранится в
  `CURRENT_PROJECT_MAP §1.4`; credentials/key/SSH transport details туда не
  переносятся. Любой профиль перепроверяется перед privileged mutation.

## Public repository security gate

Repository visibility является owner decision. Для public repository перед
security checkpoint обязателен full-history secret scan approved tool'ом
(gitleaks/trufflehog или эквивалент), включая current tree, config/, operations/,
patch_backups/, archive/history and historical payloads.

Отсутствие scan tooling либо неполный scan = NOT CHECKED HERE, не PASS.

При обнаружении credential/secrets:
1. остановить затрагиваемую mutation;
2. revoke/rotate credential у provider;
3. удалить secret из active tree;
4. отдельно решить history rewrite/visibility;
5. проверить audit/runtime state;
6. только затем восстанавливать работу.

Source of truth и active docs определяет docs/DOCUMENTATION_INDEX_RU*.md.

## Trading-account credential separation — OWNER DECISION 2026-10-16

trading_account_id, account_connector_id and credential_ref are three different
concepts.

- trading_account_id is a stable non-secret CRIPTA identity for one exact private
  account/wallet execution scope;
- account_connector_id identifies the connector/config instance or role bound to
  that TradingAccount;
- credential_ref may point to an approved secret-store entry but must not contain
  API key, API secret, private key, session token or other secret material.

Credential rotation/revocation or connector replacement must not change
trading_account_id and must not require rewriting historical trade ownership.

Provider external_account_ref may be operationally sensitive even when not a
secret. Store/log/display only the minimum needed for exact identity and audit;
do not expose it in public documentation, screenshots or unnecessary telemetry.

One connector credential must never be silently reused for a different
TradingAccount identity. Before REAL mutation the runtime must prove that the
loaded credential/connector resolves to the same exact TradingAccount expected
by StrategyExecutionBinding and AccountStateGeneration.

DEX private keys/signing material follow the same rule: public wallet/venue
identity may be referenced where required, while signing secrets remain only in
approved secret storage.

## Internal CRIPTAUser isolation and future self-service enrollment — OWNER DECISION 2026-10-25

ARCH §49.7 owns the product-user-to-TradingAccount ownership and privacy boundary; GLOSSARY §33 defines CRIPTAUser and TradingAccountOwnership. SECURITY owns secret handling and least-privilege authorization. Product user, CEX MAIN/SUB, credential reference, account connector and Unix service identity are separate concepts.

Before any multi-user rollout, all private balances, orders, positions, fee/P&L records, strategy configuration/bindings, notifications and exports must default to owner-scoped visibility and mutation. Dashboard, API, Telegram, search and reports must never reveal another user's private account facts without a separately authorized, logged delegation/admin role. Ownership changes require authorization and durable attribution rather than historical rewrites.

Self-service UI/Telegram-bot enrollment requires authenticated session ownership and a verified encrypted API Key/API Secret enrollment path to the existing approved secret store. Do not solicit plaintext API secrets through normal Telegram messages, ChatGPT chats, logs or GitHub. A bot may coordinate enrollment and display owner-scoped statistics, but does not acquire trading permission or disable LIVE gates. Credential and read/trade permission checks remain venue-specific; withdrawal/transfer privileges are not issued by default.

CONCEPT ONLY: no user registry, tenancy isolation, bot, DB migration, key change, deploy or LIVE permission is claimed.
