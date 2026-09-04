# CRIPTA

CRIPTA — production-платформа для причинного наблюдения рынка, оценки среды,
торговых стратегий, исполнения на Bybit, сопровождения позиций и воспроизводимой
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
быть с ним синхронизирован. Installed runtime, PostgreSQL и Bybit проверяются
отдельно. `C:\cripta`, старые ZIP и чаты не являются source of truth.

Проверенный source checkpoint на 2026-09-05:
`22f1ed07ec34a4713f23d4d196765ded545ec610`. Production version: V36.1.11.
Последний runtime checkpoint и дата его evidence указаны в текущей карте; их
нельзя выдавать за непрерывно наблюдаемое состояние «сейчас».

## Основные слои

Mayak наблюдает рынок, Dispatcher даёт профильный advisory-контекст, стратегия и
Entry принимают причинное решение до fill, Execution выполняет биржевые мутации и
создаёт durable handoff, Exit сопровождает позицию, Risk ограничивает денежный
риск, Position Supervisor только наблюдает и советует. PostgreSQL хранит
операционную и аналитическую историю, Bybit остаётся live exchange truth.

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
Mayak и Dispatcher не торгуют. Рыночные права отдельного Global/Fleet Safety слоя
на `BLOCK_NEW_ENTRIES` или `EMERGENCY_CLOSE` не утверждены:
`OWNER_DECISION_REQUIRED`.

## Исторический Windows Workbench

Старые инструкции Windows Workbench и ранние задания сохранены для provenance в
`docs/history/windows_workbench/` и `docs/history/tasks/`. Они не являются
текущими production-инструкциями.
