# PASS 6 — Windows release gate

Дата: 14 августа 2026  
Workbench: `0.6.0`  
Автоматические стратегии: `0.2.0`

## Цель

Подготовить воспроизводимую Windows x64 one-file сборку без включения торговых POST,
секретов и локальных артефактов. Финальное закрытие прохода выполняется только после
успешного запуска release gate на Windows 10 x64 владельца проекта.

## Реализовано

1. PyInstaller recipe оставлен one-file/windowed, UPX отключён.
2. Добавлен `--gui-smoke`: Replay GUI строится, рендерится и проверяет fail-closed
   элементы (`Run` disabled, credentials disabled, `SHADOW · DISARMED`), затем закрывается.
3. Добавлен PyInstaller runtime hook для безопасного `stdout/stderr` в windowed EXE.
4. `scripts/release/_windows.ps1` выполняет Ruff, mypy, pytest, opt-in offline soak,
   чистую PyInstaller-сборку, packaged headless smoke и packaged GUI smoke.
5. Release packager проверяет AMD64 PE, создаёт SHA-256, deterministic source ZIP,
   release manifest и Windows x64 bundle.
6. Source ZIP исключает `.venv`, build/dist, SQLite, `.env`, кэши, screenshots и
   проверяется на локальные пользовательские пути.
7. В bundle входит `verify_clean_windows.ps1`: его можно запустить на другой чистой
   Windows 10/11 x64 без Python для проверки checksum + packaged headless/GUI smoke.
8. Реальный GET Bybit и любые торговые POST в проход 6 не входят.

## Авторитетная Windows-команда

```powershell
cd C:\cripta
powershell -ExecutionPolicy Bypass -File .\scripts\setup\_windows.ps1
powershell -ExecutionPolicy Bypass -File .\scripts\release\_windows.ps1
```

Финальная строка успешного прохода:

```text
PASS 6 Windows release completed successfully.
```

После неё артефакты находятся в `C:\cripta\dist`.
