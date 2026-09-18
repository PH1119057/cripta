# CRIPTA — security baseline

Этот файл содержит технические security-инварианты и не задаёт trading policy.

- Не добавлять API keys, secrets, private keys, cookies, authorization headers
  и иные credentials в repository, issue, screenshot, logs или research output.
- Credentials хранить только в утверждённых secret stores с минимально
  необходимыми правами.
- Право чтения не означает право trading mutation.
- Mainnet mutation разрешается только через действующий канонический gate и
  exact execution path; отсутствие доказанного разрешения = fail-closed.
- Неизвестные fill/qty/position/protection, stale private state, потеря
  reconciliation или обязательного state блокируют дальнейшую mutation.
- Withdrawal/transfer privileges не выдаются торговому runtime без отдельного
  owner-approved архитектурного решения.
- При подозрении на утечку: прекратить затрагиваемую mutation,
  отозвать/заменить credential у провайдера, проверить audit/runtime state и
  только затем восстанавливать работу.

Source of truth определяется `docs/DOCUMENTATION_INDEX_RU*.md`.
