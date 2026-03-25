# awg-tgbot

Telegram-бот для выдачи подписки и конфигов AmneziaWG (AWG) через уже установленный Amnezia self-hosted.

## Что умеет

- покупка и продление подписки через Telegram Stars
- выдача 2 ключей / конфигов для 2 устройств
- работа с уже поднятым AWG в Docker
- установка, переустановка, обновление, проверка версии, логи и удаление через один `awg-tgbot.sh`

## Структура

```text
awg-tgbot/
├─ awg-tgbot.sh
└─ bot/
   ├─ app.py
   ├─ handlers_user.py
   ├─ handlers_admin.py
   ├─ payments.py
   ├─ database.py
   ├─ awg_backend.py
   ├─ config.py
   ├─ helpers.py
   ├─ keyboards.py
   ├─ security_utils.py
   ├─ texts.py
   ├─ ui_constants.py
   ├─ requirements.txt
   └─ .env.example
```

## Что нужно до установки

- Ubuntu / Debian
- root-доступ
- установленный и рабочий Docker
- уже поднятый контейнер AmneziaWG / AWG
- токен Telegram-бота
- Telegram user_id администратора

## Быстрый запуск

```bash
curl -fsSL https://raw.githubusercontent.com/Just1k13/awg-tgbot/main/awg-tgbot.sh | sudo bash
```

или

```bash
wget -qO- https://raw.githubusercontent.com/Just1k13/awg-tgbot/main/awg-tgbot.sh | sudo bash
```

## Как работает меню

Если бот **не установлен**:

- `1) Установить`
  - `1) Автоматическая установка`
  - `2) Ручная установка`
- `2) Отмена / Выход`

Если бот **уже установлен**:

- `1) Переустановить`
  - `1) Автоматическая переустановка`
  - `2) Ручная переустановка`
- `2) Обновить`
- `3) Удалить`
- `4) Проверить обновления`
- `5) Статус`
- `6) Логи`

## Автоматическая установка

В авто-режиме скрипт:

- проверяет зависимости
- пытается найти AWG-контейнер
- пытается определить интерфейс, public key и endpoint
- спрашивает только:
  - `API_TOKEN`
  - `ADMIN_ID`
  - название сервера

Если `SERVER_PUBLIC_KEY` или `SERVER_IP` не удалось определить автоматически, скрипт попросит ввести только недостающие значения.

## Ручная установка

В ручном режиме дополнительно можно задать:

- `DOCKER_CONTAINER`
- `WG_INTERFACE`
- `SERVER_PUBLIC_KEY`
- `SERVER_IP`
- цены Stars
- ссылку на Amnezia
- username поддержки

## Куда всё ставится

- код: `/opt/amnezia/bot`
- бот: `/opt/amnezia/bot/bot`
- env: `/opt/amnezia/bot/.env`
- venv: `/opt/amnezia/bot/.venv`
- systemd service: `vpn-bot.service`
- install log: `/var/log/awg-tgbot-install.log`
- app log: `/var/log/awg-tgbot/bot.log`

## Повторный запуск меню

```bash
sudo awg-tgbot
```

или

```bash
sudo bash /opt/amnezia/bot/awg-tgbot.sh
```

## Полезные команды

Статус сервиса:

```bash
systemctl status vpn-bot.service --no-pager -l
```

Логи systemd:

```bash
journalctl -u vpn-bot.service -f
```

Лог приложения:

```bash
tail -f /var/log/awg-tgbot/bot.log
```

Обновление:

```bash
sudo awg-tgbot update
```

Проверка обновлений:

```bash
sudo awg-tgbot check-updates
```

Удаление:

```bash
sudo awg-tgbot remove
```

## Важно

- скрипт сам создаёт `ENCRYPTION_SECRET`, если его ещё нет
- если у сервера есть домен, лучше использовать его как endpoint
- если автоопределение внешнего адреса не сработало, укажи `SERVER_IP` вручную в формате `host:port`
- по умолчанию используется ветка `main`

## Если бот не стартует

Проверь:

1. правильный ли `API_TOKEN`
2. правильный ли `ADMIN_ID`
3. доступен ли Docker
4. существует ли контейнер AWG
5. корректны ли переменные в `/opt/amnezia/bot/.env`
6. логи:
   - `journalctl -u vpn-bot.service -f`
   - `tail -f /var/log/awg-tgbot/bot.log`
