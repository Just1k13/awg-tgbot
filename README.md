# awg-tgbot

Telegram-бот для выдачи подписки и конфигов AmneziaWG (AWG) через Amnezia self-hosted.

## Что умеет

- покупка и продление подписки через Telegram Stars
- выдача 2 ключей/конфигов для 2 устройств
- работа с уже установленным AWG в Docker
- установка, обновление, проверка версии, логи и удаление через один скрипт `awg-tgbot.sh`

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
   └─ requirements.txt
```

## Требования

Перед установкой на сервере уже должны быть:

- Ubuntu/Debian
- root-доступ
- установленный и рабочий Docker
- уже поднятый контейнер AmneziaWG / AWG
- Telegram bot token
- Telegram user_id администратора

## Быстрая установка

Запусти одной командой:

```bash
curl -fsSL https://raw.githubusercontent.com/Just1k13/awg-tgbot/main/awg-tgbot.sh | sudo bash
```

или:

```bash
wget -qO- https://raw.githubusercontent.com/Just1k13/awg-tgbot/main/awg-tgbot.sh | sudo bash
```

После запуска откроется интерактивное меню.

## Меню awg-tgbot.sh

Скрипт умеет:

- установить бот
- обновить бот
- проверить обновления
- показать статус
- показать логи
- удалить бот

После установки меню можно открыть снова так:

```bash
sudo awg-tgbot
```

или:

```bash
sudo bash /opt/amnezia/bot/awg-tgbot.sh
```

## Где ставится бот

- код: `/opt/amnezia/bot`
- bot dir: `/opt/amnezia/bot/bot`
- env: `/opt/amnezia/bot/.env`
- venv: `/opt/amnezia/bot/.venv`
- systemd service: `vpn-bot.service`
- install log: `/var/log/awg-tgbot-install.log`
- app log: `/var/log/awg-tgbot/bot.log`

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

Перезапуск:

```bash
systemctl restart vpn-bot.service
```

## Обновление

Обновить можно через меню `awg-tgbot.sh` или командой:

```bash
sudo awg-tgbot update
```

Проверить наличие обновления:

```bash
sudo awg-tgbot check-updates
```

## Удаление

Удалить можно через меню или командой:

```bash
sudo awg-tgbot remove
```

Есть 2 варианта удаления:

1. удалить только сервис и venv, но оставить код, `.env` и базу
2. удалить всё полностью

## Важно

- скрипт сам создаёт `ENCRYPTION_SECRET`, если его ещё нет
- если в репозитории нет `bot/.env.example`, установка всё равно продолжится, `.env` будет создан автоматически
- по умолчанию используется ветка `main`

## Если бот не стартует

Проверь:

1. правильный ли `API_TOKEN`
2. правильный ли `ADMIN_ID`
3. доступен ли Docker
4. существует ли контейнер AWG
5. корректны ли переменные в `/opt/amnezia/bot/.env`
6. логи `journalctl -u vpn-bot.service -f`

---

Если репозиторий приватный или у тебя другая ветка, поправь `REPO_OWNER`, `REPO_NAME` и `REPO_BRANCH` в `awg-tgbot.sh`.
