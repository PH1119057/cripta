# Проход 3 — Mainnet coordinator и desktop runtime

Дата: 14 августа 2026.

## Результат

Реализован единый Mainnet execution path `Check → Arm → Run`. Все mutating-запросы
могут пройти только через `MainnetMutationGateway`; прямой write transport не
передаётся ни стратегии, ни UI. Каждый новый процесс и каждый сброс плана начинают
работу в `SHADOW`/disarmed.

Desktop runtime умеет выполнять GET-only preflight в фоновом потоке, выпускать
короткоживущий ticket, требовать точную фразу `ARM MICRO_LIVE`, отдельно подтверждать
реальные деньги перед Run и уничтожать ticket при остановке или изменении плана.
На этом проходе preflight намеренно закрыт до привязки конкретного воспроизводимого
BackTest report к strategy/version/parameters в проходе 4.

## Что добавлено

- account-wide REST snapshot всех доступных linear settle coins и inverse contracts;
- свежий safety state, объединяющий GET-only identity/reconciliation и Public/Private
  WS health;
- durable Mainnet coordinator для entry, cancel, protection и market reduce-only close;
- подтверждение по свежему Private WS с REST fallback;
- запрет blind retry после timeout, потерянного или некорректного ответа;
- проверка attached Mark Price stop и повторная защита фактически исполненного
  количества при partial fill;
- UI-состояния `DISARMED`, `CHECKING`, `CHECKED`, `ARMED`, `RUNNING`, `PAUSED`,
  `EXPIRED`, `KILL_SWITCH`, `BLOCKED`;
- Mainnet Stop, cancel entries, cancel non-protective, flatten и emergency route через
  тот же coordinator.

## Проверки в среде разработки

- Ruff: без ошибок.
- mypy strict: без ошибок в 85 исходных файлах.
- pytest: 233 passed; GUI smoke пропущен в Linux из-за отсутствия PySide6, soak
  запускается отдельно.
- отдельный soak: 10 000 циклов, без сети и ключей.
- headless smoke: без сети и ключей.

Итоговая Windows-проверка выполняется `scripts\check_windows.ps1`. Ожидается 234
passed и один пропущенный soak-тест, поскольку в Windows-окружении PySide6 установлен.

## Граница безопасности

Во время разработки не читались реальные API-ключи, не выполнялся GET к аккаунту и
не отправлялся торговый POST. До прохода 4 Mainnet Check останавливается до создания
сетевого подключения сообщением об отсутствии точной historical binding. До прохода 7
не следует запускать реальный GET-only connection test. Первый Micro-Live POST не
входит в семь технических проходов и требует отдельного решения владельца аккаунта.
