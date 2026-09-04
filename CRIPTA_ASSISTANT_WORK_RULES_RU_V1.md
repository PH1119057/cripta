# CRIPTA — правила работы для ChatGPT / разработчика

Версия: 1.0 · 2026-09-05  
Назначение: только процесс разработки, patch/install, Git, проверки и работа с консолью.  
Архитектура торговых слоёв вынесена в `CRIPTA_ARCHITECTURE_RULES_RU_V1.md`.

## 1. Не выходить за рамки задачи
Сначала определить scope: стабилизация, инфраструктура, логика, research или документация. При стабилизации нельзя самовольно переходить к изменению Entry/Exit/Risk/Research. Найденное несоответствие — только записать и вынести в отдельную задачу.

## 2. Source of truth
Текущий source of truth: `/srv/cripta/source_checkout` + GitHub `PH1119057/cripta:main`. Старый `C:\cripta`, старые ZIP/чаты/manifests вторичны. Текущий baseline V36.1.11: `22f1ed07ec34a4713f23d4d196765ded545ec610`.

## 3. Каждый patch имеет точный baseline
ZIP обязан указывать baseline, prerequisites, изменяемые файлы, `EXISTING_MODIFY/NEW_FILE`, resulting version, «изменяет / не изменяет». Классификация путей — только относительно baseline, не по памяти.

## 4. Проверять post-patch overlay
Проверять финальное состояние: baseline -> temp overlay -> реальные относительные пути -> strongest available gate. По возможности: syntax/py_compile, Ruff, mypy, новые tests, затронутые tests, full pytest, smoke/gates.

## 5. Проверять именно финальный ZIP
После последней упаковки проверить SHA256, ZIP CRC, структуру, внутренние SHA, версии/metadata, отсутствие wrapper и мусора. После расчёта SHA ZIP больше не менять.

## 6. Server ZIP только через installer rail
В корне ZIP: `MANIFEST.json`, `install.sh`, `SHA256SUMS.txt`, без wrapper. Установка только: `sudo /usr/local/sbin/cripta-apply-incoming <zip>`. Sidecar: `<sha><two spaces><basename.zip>`.

## 7. Installer fail-closed
Порядок: precheck -> temp overlay -> tests -> backup -> live mutation. Ошибка до live mutation не должна менять source/live/reports/user data.

## 8. Ошибки подготовки не перекладывать на пользователя
Ruff/mypy/syntax/import/package/version/path ошибки = ошибка подготовки patch. Не выдавать длинную цепочку ручных правок; пересобрать чистый consolidated patch.

## 9. Один класс ошибки — проверить весь класс
`smallint/boolean`, status tokens, versions, path mapping, file classification и т.п. После первого обнаружения искать все аналогичные места, а не исправлять один симптом.

## 10. Не подгонять tests
При падении решить: неверен production или устарел test contract. Нельзя менять expectation ради зелёного pytest и нельзя возвращать старую архитектуру только ради старого теста.

## 11. Запрещённая production-логика не должна лежать под `if False`
Если функция `NOT_PROVEN / DISABLED_BY_CONTRACT`, исполняемого production path быть не должно, если владелец явно не утвердил обратное.

## 12. Среда инструментов должна быть детерминирована
Не выбирать случайный Python/venv. Явно определять Python, uv, pytest, Ruff, mypy и test environment. Если инструмент недоступен — fail before live mutation.

## 13. LF/CRLF не путать с code drift
На сервере: `core.autocrlf=false`, `core.eol=lf`. Различать byte-identical, newline-only и real content drift. Не нормализовать source молча.

## 14. Source/live mapping не угадывать
Пути live deployment брать только из installer/deployment contract. Verifier должен использовать тот же mapping.

## 15. Git sync — только exact changeset
Запрещено `git add -A`. Порядок: status -> классификация -> exact expected set -> `git add -- <explicit files>` -> staged check -> commit -> push -> verify remote SHA. Неизвестный untracked path = hard stop.

## 16. Один machine state — один canonical token
Runtime/installer/tests не должны использовать разные строки для одного состояния. То же для PATCH_ID, VERSION, RESULTING_VERSION и safety states.

## 17. Консоль: только одна строка или готовый файл
Простая операция — одна физическая строка. Сложная (`if/for/heredoc/Python/SQL/сложный quoting`) — создать `.sh/.py/.ps1`, передать файл, затем дать одну строку запуска. Большие многострочные блоки для paste запрещены. Если paste уже один раз съехал — до конца сессии только эти два формата.

## 18. После stable checkpoint остановиться
Если installer PASS, сервисы active, source/live match, GitHub synchronized, tracked worktree clean — зафиксировать checkpoint и не начинать новый аудит без отдельной команды владельца.

## 19. CHECKED и NOT CHECKED HERE разделять всегда
В отчёте перечислять только реально выполненные проверки. Не говорить «проверено» после одного py_compile или isolated payload test.

## 20. Долгие jobs должны быть наблюдаемыми
Stage, processed/total, %, elapsed, ETA, heartbeat ~20–30 сек, resume/cache/idempotency где возможно. Не пересчитывать тяжёлые данные без причины.

## 21. Если код конфликтует с концепцией — hard stop
Нельзя тихо менять торговую логику внутри «технического» patch. Либо код приводится к действующей архитектуре, либо владелец сначала меняет архитектурный контракт. При неясном baseline/scope/schema/path/test contract — не мутировать систему по предположению.

---

## Что вынесено в архитектурный файл
Entry/Execution/Exit/Risk, MAYAK, Position Supervisor, Bybit live truth, restart/reconciliation, SHADOW->MICRO_LIVE->LIVE, research/OOS/holdout, leverage/BE, data provenance, portfolio validation и UI/audit.
