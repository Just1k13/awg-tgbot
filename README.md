# awg-tgbot

Telegram-бот для продажи доступа и выдачи конфигов AmneziaWG (AWG) на уже установленном self-hosted Amnezia сервере.

## Что умеет

- покупка и продление подписки через Telegram Stars
- выдача 2 конфигов / `vpn://` ключей для 2 устройств
- работа с уже поднятым контейнером AWG в Docker
- установка, переустановка, обновление, удаление, статус и логи через один скрипт `awg-tgbot.sh`

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

## Требования

На сервере должны быть:

- Ubuntu / Debian
- root-доступ
- установленный Docker
- уже поднятый и рабочий контейнер AmneziaWG / AWG
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

## Как работает installer

Если бот не установлен:

- `1) Установить`
- `2) Отмена / Выход`

После выбора установки:

- `1) Автоматическая установка`
- `2) Ручная установка`

### Автоматическая установка

Скрипт сам пытается определить:

- Docker контейнер AWG
- интерфейс (`awg0` и т.д.)
- путь к конфигу внутри контейнера
- `SERVER_PUBLIC_KEY`
- внешний endpoint `SERVER_IP`

И спрашивает только:

- `API_TOKEN`
- `ADMIN_ID`
- `SERVER_NAME`

Если что-то определить не удалось, попросит ввести только недостающие поля.

### Ручная установка

Попросит ввести основные параметры AWG и настройки бота вручную.

## Где ставится бот

- код: `/opt/amnezia/bot`
- бот: `/opt/amnezia/bot/bot`
- env: `/opt/amnezia/bot/.env`
- venv: `/opt/amnezia/bot/.venv`
- service: `vpn-bot.service`
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

Статус:

```bash
systemctl status vpn-bot.service --no-pager -l
```

Логи сервиса:

```bash
journalctl -u vpn-bot.service -f
```

Логи приложения:

```bash
tail -f /var/log/awg-tgbot/bot.log
```

## Важно

- `API_TOKEN` и `ADMIN_ID` при установке вводятся руками и не подставляются автоматически
- если бот пишет `Telegram server says - Unauthorized`, токен нужно перевыпустить в BotFather и обновить в `.env`
- `ENCRYPTION_SECRET` должен быть задан в `.env`; установщик создаёт его автоматически
- `config.py` больше не делает интерактивный ввод при импорте и не переписывает `.env` сам по себе
