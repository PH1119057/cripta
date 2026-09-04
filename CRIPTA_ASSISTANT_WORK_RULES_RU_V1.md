# CRIPTA — правила работы для ChatGPT / разработчика

Версия: 1.1 · 2026-09-05
Назначение: процесс разработки, patch/install, Git, проверки, консоль и обязательная дисциплина архитектурных изменений.
Верхняя архитектура проекта вынесена в `CRIPTA_ARCHITECTURE_RULES_RU_V1.md`.

## 1. Не выходить за рамки задачи

Сначала определить scope: стабилизация, инфраструктура, логика, research или документация.

При стабилизации нельзя самовольно переходить к изменению прикладной торговой архитектуры, Strategy, Entry, Exit, Execution, MAYAK, Dispatcher или исследовательской логики. Найденное несоответствие — записать и вынести в отдельную задачу.

## 2. Source of truth

Текущий source of truth: `/srv/cripta/source_checkout` + GitHub `PH1119057/cripta:main`.

Старый `C:\cripta`, старые ZIP, чаты, manifests и локальные заметки вторичны.

Фактический текущий source checkpoint определяется проверенным равенством GitHub `main` и `/srv/cripta/source_checkout`. Исторический baseline не выдавать за текущий HEAD.

## 3. Перед архитектурно чувствительной работой перечитать верхние контракты

Перед изменением MAYAK, Dispatcher, Strategy / Entry / Exit / Execution, связей прикладного и технического контуров, account/capital context, research/OOS/holdout, re-arm/MICRO_LIVE/LIVE, ownership, safety contract или signal lifecycle обязательно перечитать:

1. `CRIPTA_ASSISTANT_WORK_RULES_RU_V1.md`;
2. `CRIPTA_ARCHITECTURE_RULES_RU_V1.md`;
3. `docs/PROJECT_ARCHITECTURE_RU.md`;
4. контракты реально затрагиваемых компонентов.

## 4. Risk не является самостоятельным верхним архитектурным слоем

Термин `Risk` может существовать в коде, исследованиях, исторических именах и специализированных формулах, но разработчик не имеет права из этого делать отдельного верхнеуровневого владельца торгового решения.

Архитектурная трактовка задаётся `CRIPTA_ARCHITECTURE_RULES_RU_V1.md`:

- параметры использования капитала, размер позиции, плечо, stop, допустимая просадка и правила удержания принадлежат Strategy;
- состояние доступных торговых средств публикуется Dispatcher на основе фактического состояния подключённого торгового счёта;
- техническая безопасность исполнения принадлежит техническому контуру / Execution;
- наблюдаемые опасные состояния рынка принадлежат MAYAK как факты рынка.

Нельзя вводить новый top-level `Risk` layer без отдельного решения владельца и новой версии архитектурного контракта.

## 5. Каждый patch имеет точный baseline

ZIP обязан указывать baseline, prerequisites, изменяемые файлы, `EXISTING_MODIFY/NEW_FILE`, resulting version, «изменяет / не изменяет». Классификация путей — только относительно baseline, не по памяти.

## 6. Проверять post-patch overlay

Проверять финальное состояние: baseline -> temp overlay -> реальные относительные пути -> strongest available gate.

По возможности: syntax/py_compile, Ruff, mypy, новые tests, затронутые tests, full pytest, smoke/gates.

## 7. Проверять именно финальный ZIP

После последней упаковки проверить SHA256, ZIP CRC, структуру, внутренние SHA, версии/metadata, отсутствие wrapper и мусора. После расчёта SHA ZIP больше не менять.

## 8. Server ZIP только через installer rail

В корне ZIP: `MANIFEST.json`, `install.sh`, `SHA256SUMS.txt`, без wrapper.

Установка только:

```text
sudo /usr/local/sbin/cripta-apply-incoming <zip>
```

Sidecar:

```text
<sha><two spaces><basename.zip>
```

## 9. Installer fail-closed

Порядок:

```text
precheck -> temp overlay -> tests -> backup -> live mutation
```

Ошибка до live mutation не должна менять source/live/reports/user data.

## 10. Ошибки подготовки не перекладывать на пользователя

Ruff/mypy/syntax/import/package/version/path ошибки = ошибка подготовки patch.

Не выдавать длинную цепочку ручных правок; пересобрать чистый consolidated patch.

## 11. Один класс ошибки — проверить весь класс

После первого обнаружения класса ошибки искать все аналогичные места, а не исправлять один симптом.

## 12. Не подгонять tests

При падении определить: неверен production или устарел test contract.

Нельзя менять expectation ради зелёного pytest и нельзя возвращать старую архитектуру только ради старого теста.

## 13. Запрещённая production-логика не должна лежать под `if False`

Если функция `NOT_PROVEN / DISABLED_BY_CONTRACT`, исполняемого production path быть не должно, если владелец явно не утвердил обратное.

## 14. Среда инструментов должна быть детерминирована

Не выбирать случайный Python/venv. Явно определять Python, uv, pytest, Ruff, mypy и test environment.

Если инструмент недоступен — fail before live mutation.

## 15. LF/CRLF не путать с code drift

На сервере: `core.autocrlf=false`, `core.eol=lf`.

Различать byte-identical, newline-only и real content drift. Не нормализовать source молча.

## 16. Source/live mapping не угадывать

Пути live deployment брать только из installer/deployment contract. Verifier должен использовать тот же mapping.

## 17. Git sync — только exact changeset

Запрещено `git add -A` и `git add .`.

Порядок: status -> классификация -> exact expected set -> `git add -- <explicit files>` -> staged check -> commit -> push -> verify remote SHA.

Неизвестный untracked path = hard stop.

## 18. Один machine state — один canonical token

Runtime/installer/tests не должны использовать разные строки для одного состояния.

## 19. Консоль: только одна строка или готовый файл

Простая операция — одна физическая строка.

Сложная (`if/for/heredoc/Python/SQL/сложный quoting`) — готовый `.sh/.py/.ps1` и одна строка запуска.

## 20. После stable checkpoint остановиться

Если installer PASS, сервисы active, source/live match, GitHub synchronized, tracked worktree clean — зафиксировать checkpoint и не начинать новый аудит без отдельной команды владельца.

## 21. CHECKED и NOT CHECKED HERE разделять всегда

В отчёте перечислять только реально выполненные проверки.

## 22. Долгие jobs должны быть наблюдаемыми

Stage, processed/total, %, elapsed, ETA, heartbeat ~20–30 сек, resume/cache/idempotency где возможно.

## 23. Если код конфликтует с концепцией — hard stop

Нельзя тихо менять торговую логику внутри «технического» patch.

Порядок:

```text
OWNER DECISION
-> ARCHITECTURE DOCUMENT
-> VERSION
-> ARCHITECTURE TEST
-> CODE
-> CHECKS
-> DEPLOY
```

Если текущее устройство кода расходится с архитектурой — это finding, а не разрешение автоматически переписать код.

## 24. Прикладной и технический контуры не смешивать

Верхняя прикладная архитектура содержит пять уровней:

```text
MAYAK -> DISPATCHER -> STRATEGY -> EXECUTION -> EXCHANGE
```

`Entry` и `Exit` являются специализированными частями `STRATEGY`.

Технический контур обеспечивает данные, связь, хранение, наблюдаемость, восстановление и безопасное исполнение. Его компоненты не становятся от этого новыми верхнеуровневыми торговыми слоями.

Точные границы и связи читать в `CRIPTA_ARCHITECTURE_RULES_RU_V1.md` и `docs/PROJECT_ARCHITECTURE_RU.md`.
