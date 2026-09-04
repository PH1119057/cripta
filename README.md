# CRIPTA

CRIPTA — production-платформа для причинного наблюдения рынка, оценки среды,
торговых стратегий, исполнения на подключённой бирже, сопровождения позиций и воспроизводимой
аналитики.

Сначала прочитайте:

- [Правила работы](CRIPTA_ASSISTANT_WORK_RULES_RU_V1.md)
- [Архитектурные правила](CRIPTA_ARCHITECTURE_RULES_RU_V1.md)
- [Текущая карта](docs/CURRENT_PROJECT_MAP_RU.md)
- [Управление изменениями](docs/PROJECT_GOVERNANCE_RU.md)
- [Авторитетность документов](docs/DOCUMENT_AUTHORITY_RU.md)

## Source of truth и checkpoint

GitHub `PH1119057/cripta:main` — общий канонический source checkpoint;
`/srv/cripta/source_checkout` — канонический server checkout, который обязан
быть с ним синхронизирован. Installed runtime, PostgreSQL и подключённая биржа проверяются
отдельно. `C:\cripta`, старые ZIP и чаты не являются source of truth.

Baseline перед документационной ревизией 2026-09-05:
`22f1ed07ec34a4713f23d4d196765ded545ec610`. Текущий source checkpoint — commit,
содержащий этот README, при проверенном равенстве GitHub main и server checkout.
Production version: V36.1.11.
Последний runtime checkpoint и дата его evidence указаны в текущей карте; их
нельзя выдавать за непрерывно наблюдаемое состояние «сейчас».

## Основные слои

Каноническая пятиуровневая модель:

```text
MAYAK -> DISPATCHER -> STRATEGY(ENTRY/EXIT) -> EXECUTION -> EXCHANGE
```

Strategy владеет политикой использования капитала, размера, плеча, stop,
drawdown и holding; Risk не является отдельным верхним слоем. Technical support
contour, включая Position Supervisor и Analyst, наблюдает и обслуживает систему,
но не образует дополнительного trading layer. PostgreSQL хранит операционную и
аналитическую историю, подключённая биржа остаётся live exchange truth.

## Установка patch

Единственный канонический server ZIP rail:

```text
sudo /usr/local/sbin/cripta-apply-incoming <zip>
```

ZIP содержит в корне `MANIFEST.json`, `install.sh`, `SHA256SUMS.txt`, без wrapper
directory. Полный контракт описан в
[CODEX_AUTOMATION_AND_PATCH_INSTALL_RU.md](docs/CODEX_AUTOMATION_AND_PATCH_INSTALL_RU.md).

## Live safety

Неизвестное обязательное exchange/private state, сбой часов или reconciliation,
неизвестные qty/fill/protection и owner emergency kill остаются fail-closed.
MAYAK и Dispatcher не торгуют. Общерыночное состояние является advisory
indicator/context Dispatcher: разные Strategy могут интерпретировать его
по-разному. Отдельного market-driven владельца `CLOSE ALL` в верхней архитектуре
нет. Operational safety остаётся отдельным техническим fail-closed контуром.

## Исторический Windows Workbench

Старые инструкции Windows Workbench и ранние задания сохранены для provenance в
`docs/history/windows_workbench/` и `docs/history/tasks/`. Они не являются
текущими production-инструкциями.
