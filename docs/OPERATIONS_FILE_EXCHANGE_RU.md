# OPERATIONS FILE EXCHANGE — проект «Крипта»

**Версия:** 1.1
**Дата:** 2026-09-05
**Статус:** operational contract.

## 1. Назначение incoming

`/srv/cripta-share/incoming` — штатная входная папка для ZIP-пакетов, которые владелец передаёт на сервер для установки/разбора. На Windows эта папка может быть отображена как сетевой ресурс, например `K:\\incoming`; буква диска является клиентским отображением и не является серверным source of truth.

Обычный rail:

```text
ChatGPT собирает ZIP + отдельный audit SHA256
→ владелец кладёт ZIP в Windows network share incoming; `.zip.sha256` можно положить рядом, но это не обязательное условие запуска
→ тот же файл появляется в /srv/cripta-share/incoming
→ installer распаковывается/запускается оттуда
→ отчёты пишутся в /srv/cripta-share/operations и /srv/cripta-share/reports по контракту конкретного release
```

## 2. Граница доступа ChatGPT

ChatGPT не получает прямой файловый доступ к пользовательскому Windows-диску `K:` или к серверному `/srv/cripta-share/incoming` только потому, что папка видна владельцу в Проводнике. ChatGPT может видеть:

- файлы, загруженные в чат / File Library;
- данные через явно подключённые инструменты/коннекторы;
- содержимое сервера, которое пользователь передал выводом команд или отдельным доступным инструментом.

Поэтому отсутствие прямого просмотра `incoming` со стороны ChatGPT не отменяет `incoming` как штатный канал доставки. Не надо заменять этот rail копированием в `/home/alex`, если владелец уже положил ZIP в `incoming` и серверная сторона доступна установщику.

## 3. Права

Если интерактивный пользователь `alex` не может `cd /srv/cripta-share/incoming`, это вопрос server permissions/ACL, а не причина менять архитектуру доставки. Нельзя молча обходить это постоянным новым workflow через home. Для установки допустим запуск с требуемыми правами (`sudo`) либо исправление ACL отдельным operational change.

## 4. Source-of-truth границы

```text
GitHub main                  = canonical source-code checkpoint
/srv/cripta/source_checkout  = canonical server source checkout
/srv/cripta/...              = installed production runtime
PostgreSQL                   = canonical persisted runtime/history
Bybit                        = live exchange truth
/srv/cripta-share/incoming   = package delivery/staging, НЕ source of truth
/srv/cripta-share/operations = operational artifacts / install reports / backups
/srv/cripta-share/reports    = reports
```

## 5. Правило для будущих patch releases

Инструкция установки должна по умолчанию использовать `incoming`. Если ChatGPT сам не может просмотреть эту папку, он должен честно сказать об ограничении доступа, но не придумывать другой постоянный rail. Если серверные права мешают владельцу работать с `incoming`, это фиксируется как отдельная operational проблема.

## 6. Единый серверный runner для ZIP из incoming

Целевой workflow после первого успешного запуска server package runner:

```text
владелец кладёт PATCH.zip в K:\\incoming; PATCH.zip.sha256 — рекомендуемый audit companion, но runner не требует его наличия
→ файл появляется в /srv/cripta-share/incoming
→ владелец запускает одну команду: sudo /usr/local/sbin/cripta-apply-incoming PATCH.zip
→ runner сам создаёт private staging в operations
→ распаковывает ZIP
→ проверяет ZIP CRC, вычисляет и сохраняет archive SHA256; если рядом есть `.zip.sha256`, сверяет и его; затем проверяет SHA256SUMS/manifest
→ запускает clean overlay + Ruff/pytest/project gate + DB precheck
→ только после PASS применяет tested commit к production
→ пишет отчёт в operations
```

Runner не является watcher-ом и не ставит патчи автоматически по факту появления ZIP: применение всегда начинается отдельным явным действием владельца. Это сохраняет owner control над live-системой.

Постоянный canonical runner уже установлен. Новый patch не bootstrap-ит
альтернативный runner и не приносит второй равноправный installer workflow.
Дальнейшие пакеты запускаются только как
`sudo /usr/local/sbin/cripta-apply-incoming PATCH.zip`.

Контракт ZIP: в корне находятся `MANIFEST.json`, `install.sh` и
`SHA256SUMS.txt`, wrapper directory отсутствует. Формат sidecar:
`<sha256><two spaces><basename.zip>`.


## 7. Свободное место и целостность доставки

`incoming` находится на том же серверном filesystem, поэтому имя файла в Windows share не доказывает успешную запись. Если серверный filesystem заполнен, SMB/SFTP может успеть создать имя или нулевой/частичный файл, после чего ZIP/PDF становится повреждённым или исчезает.

Для серьёзных patch releases серверный installer обязан fail-closed проверить запас места **до** создания overlay/test toolchain. Текущий минимальный operational floor: 2 GiB свободного места на filesystem `operations`; при меньшем запасе production не меняется.

При ошибках `End-of-central-directory`, `write remote ... Failure`, исчезающих файлах или 0-byte copy сначала проверяется `df -h /`; нельзя бесконечно пересобирать patch, если transport/storage не может записать байты.

Временные archive-build каталоги `.archive_jobs` не являются source of truth. Их автоматическая ротация/удаление после доказанного появления идентичного final snapshot в `reports` должна решаться отдельным operational patch; P1 сам архиватор не меняет.
