# Security policy

- Не добавляйте API keys, secrets, подписи, cookies или authorization headers в
  репозиторий, issue, screenshot и SQLite.
- Mainnet-профиль `BotW-Mainnet` хранится только в Windows Credential Manager.
- Wallet, Withdraw, AccountTransfer и SubMemberTransfer запрещены для ключа Workbench
  и блокируют arming; SpotTrade/OptionsTrade считаются лишними правами.
- Наличие Read/Write ключа не является arming. После запуска разрешён только `SHADOW`;
  любая Mainnet-мутация в нём считается критической уязвимостью.
- При утечке немедленно отзовите ключ на Bybit, остановите Workbench, сохраните журнал
  без секретов для расследования и выполните ротацию по `RUNBOOK.md`.
