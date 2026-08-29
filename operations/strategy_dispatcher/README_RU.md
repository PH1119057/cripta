# Диспетчер стратегий — эксплуатационный контур D3-D6

Этот каталог содержит **шаблон** systemd unit. Инсталлятор патча не копирует его в
`/etc/systemd/system`, не делает `daemon-reload`, не включает и не запускает сервис.

До отдельной серверной команды Диспетчер существует только как установленный код.

Пассивный сервис:

- читает только `/var/lib/cripta/mayak_v2/status.json`;
- читает профили из `/srv/cripta/config/strategy_dispatcher/profiles`;
- пишет только `/var/lib/cripta/strategy_dispatcher`;
- не импортирует Entry/Exit/Risk/Execution/Position Supervisor;
- не имеет сетевого клиента;
- не имеет ключей Bybit;
- не имеет PostgreSQL-прав записи;
- не имеет торговых команд.

Все поставляемые reference profiles имеют `"enabled": false`. Поэтому даже после
ручного запуска сервиса без отдельного включения профилей он будет только сохранять
снимок адаптера и `profile_count = 0`.
