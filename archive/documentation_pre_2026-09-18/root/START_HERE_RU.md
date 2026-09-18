# Начать здесь

Это полный исходный архив кандидата прохода 6. После распаковки проект должен находиться
в `C:\cripta`.

## Безопасная замена папки

1. Закройте Workbench и убедитесь, что его процесса нет в Диспетчере задач.
2. Переименуйте прежнюю папку, например в `C:\cripta_backup_before_pass6`.
3. Распакуйте архив **в `C:\`**.
4. Проверьте наличие `C:\cripta\pyproject.toml`.

Профиль `BotW-Mainnet` остаётся в Windows Credential Manager. API key/secret в исходный
архив и release bundle не включаются.

## Проход 6

Откройте обычный 64-bit PowerShell:

```powershell
cd C:\cripta
powershell -ExecutionPolicy Bypass -File .\scripts\setup\_windows.ps1
powershell -ExecutionPolicy Bypass -File .\scripts\release\_windows.ps1
```

Release gate сам повторяет Ruff, mypy, pytest, offline soak, затем строит one-file EXE
и запускает уже **упакованный** EXE в headless и offscreen-GUI smoke. Bybit не нужен,
ключи не читаются, профиль принудительно Replay, live/testnet execution выключены.

Ожидаемый итог:

```text
PASS 6 Windows release completed successfully.
```

После успеха пришлите сюда весь вывод PowerShell. В `dist` будут EXE, SHA-256,
source ZIP, manifest и `BybitStrategyWorkbench-0.6.0-windows-x64.zip`.

Для дополнительной проверки на другой чистой Windows 10/11 x64 распакуйте только
release bundle и запустите `verify_clean_windows.ps1`; Python там не нужен.

## Важная граница

Workbench теперь `0.6.0`, стратегии остаются `0.2.0`. Historical eligibility связан с
версией кода, поэтому перед Micro-Live будет создан новый exact BackTest report уже для
`0.6.0`. Проход 6 не выполняет реальный GET Bybit и не включает торговый POST.

## Проход 7 — реальный GET-only acceptance

После `setup` и `check` запускается отдельный скрипт, который принудительно держит
`BYBIT_WORKBENCH_ALLOW_LIVE_TRADING=0` и только читает Mainnet:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\accept_mainnet\_windows.ps1 `
  -Symbol UNIUSDT `
  -Endpoint https://api.bybit.kz
```

Результат: `var\mainnet_acceptance.json` + `.sha256`. В JSON нет API key, secret и
самих IP-адресов. Его можно передать для ревью перед подготовкой Micro-Live manifest.
