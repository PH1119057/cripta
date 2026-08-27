# Восстановление Cripta

Резервные копии находятся в `/data/cripta/backups/system/<UTC timestamp>`.
Они содержат проект, отчёты, PostgreSQL и конфигурацию служб. Тяжёлый каталог
`/data/cripta/datasets` намеренно не копируется: он проверяется manifest-файлом и
может быть повторно скачан.

## Проверка снимка

В каталоге снимка выполнить от root:

```bash
sha256sum --check SHA256SUMS
pg_restore --list cripta.pgdump >/dev/null
tar -tzf project.tar.gz >/dev/null
tar -tzf configuration.tar.gz >/dev/null
```

## Порядок полного восстановления

1. Установить Ubuntu, PostgreSQL 16, Nginx и Python-зависимости.
2. Подключить SSD к `/data/cripta` согласно сохранённому `/etc/fstab`.
3. Проверить `SHA256SUMS` выбранного снимка.
4. Остановить службы `cripta-*` и Nginx.
5. Распаковать `project.tar.gz` и `configuration.tar.gz` в `/`.
6. Создать роль и пустую базу PostgreSQL:

```bash
sudo -u postgres createuser cripta
sudo -u postgres createdb --owner=cripta cripta
sudo -u postgres pg_restore --dbname=cripta --clean --if-exists cripta.pgdump
```

7. Проверить владельцев каталогов `cripta:cripta`, выполнить `systemctl daemon-reload`.
8. Запустить PostgreSQL, Nginx и read-only службы. Торговое исполнение оставить
   заблокированным до reconciliation с Bybit.
9. Проверить `/healthz`, веб-интерфейс, свежесть WebSocket, баланс, позиции и заявки.
10. Только после совпадения exchange truth разрешать следующие режимы исполнения.

Перед реальным восстановлением команды должны выполняться на новом или отдельно
подготовленном сервере. Не распаковывать конфигурацию поверх работающей системы без
остановки служб и выбранной точки возврата.
