# CRIPTA — security baseline

Этот файл содержит technical security invariants и не задаёт trading policy.

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
