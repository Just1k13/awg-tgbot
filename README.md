# awg-tgbot

Telegram-бот для продажи доступа и автоматической выдачи конфигов AmneziaWG (AWG) на уже установленном self-hosted сервере с AmneziaWG.

## Что это такое

Проект состоит из двух частей:

- `awg-tgbot.sh` — единый интерактивный installer / updater / remover / maintenance-скрипт.
- `bot/` — Telegram-бот на `aiogram`, который продаёт подписки за Telegram Stars, продлевает доступ, выдаёт пользователю `.conf` и `vpn://`-ключи, а администратору даёт панель управления.

Проект рассчитан не на поднятие AWG с нуля, а на интеграцию с **уже работающим** контейнером AmneziaWG/AWG.

---

## Возможности

### Для пользователя

- покупка доступа через **Telegram Stars**;
- продление уже активной подписки;
- профиль с датой окончания доступа;
- выдача до `CONFIGS_PER_USER` конфигов на пользователя;
- отправка:
  - `vpn://` ключа для быстрого импорта;
  - `.conf` файла для ручного подключения;
- инструкция по подключению;
- кнопка поддержки.

### Для администратора

- встроенная админ-панель в боте;
- статистика по пользователям, ключам и свободным IP;
- просмотр последних пользователей;
- выдача доступа вручную;
- отзыв доступа;
- полное удаление пользователя;
- проверка синхронизации **AWG ↔ база**;
- поиск и очистка **orphan peer**;
- журнал действий (`audit_log`);
- массовая рассылка по пользователям;
- резервная копия БД без секретов.

### Для сервера

- один скрипт для:
  - установки,
  - переустановки,
  - обновления,
  - удаления,
  - проверки обновлений,
  - просмотра статуса,
  - просмотра логов;
- автоопределение контейнера AWG, интерфейса, `SERVER_PUBLIC_KEY`, endpoint и AWG-параметров;
- создание и обновление `systemd`-сервиса;
- хранение локального SHA установленной версии для проверки обновлений.

---

## Как работает проект

1. Пользователь запускает бота и открывает меню.
2. Через Telegram Stars покупает тариф на 7 или 30 дней.
3. После успешной оплаты бот:
   - записывает платёж в БД;
   - переводит платёж в состояние обработки;
   - продлевает/активирует подписку;
   - создаёт недостающие peer в AWG;
   - сохраняет зашифрованные клиентские секреты в БД;
   - выдаёт пользователю `vpn://` и `.conf`.
4. Фоновый worker периодически очищает просроченные подписки и удаляет peer из AWG.
5. Администратор может вручную управлять пользователями и чистить orphan peer.

---

## Архитектура

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

### Назначение основных файлов

- `app.py` — точка входа, регистрация роутеров, middleware, старт polling и фоновой очистки.
- `config.py` — чтение `.env`, дефолты, автоопределение AWG-окружения, валидация обязательных переменных.
- `payments.py` — выставление инвойсов Telegram Stars и обработка успешной оплаты.
- `handlers_user.py` — пользовательское меню: профиль, конфиги, покупка, инструкция, поддержка.
- `handlers_admin.py` — админ-команды, статистика, рассылки, revoke/delete/sync/cleanup.
- `awg_backend.py` — работа с `docker exec`, peer, IP, генерацией конфигов и `vpn://`.
- `database.py` — схема SQLite, миграции через `ensure_column`, audit log и служебные таблицы.
- `security_utils.py` — шифрование чувствительных данных через Fernet-совместимый ключ, полученный из `ENCRYPTION_SECRET`.

---

## Требования

На сервере должны быть:

- Ubuntu / Debian;
- root-доступ;
- установленный Docker;
- уже поднятый и рабочий контейнер AmneziaWG / AWG;
- токен Telegram-бота;
- `Telegram user_id` администратора.

---

## Быстрый запуск

```bash
curl -fsSL https://raw.githubusercontent.com/Just1k13/awg-tgbot/main/awg-tgbot.sh | sudo bash
```

или

```bash
wget -qO- https://raw.githubusercontent.com/Just1k13/awg-tgbot/main/awg-tgbot.sh | sudo bash
```

---

## Режимы работы installer

### Если бот не установлен

```text
1) Установить
2) Отмена / Выход
```

### Если найдены остаточные файлы старой установки

```text
1) Продолжить установку / переустановку поверх остатков
2) Удалить всё
3) Удалить всё, кроме БД и .env
0) Выход
```

### Если бот уже установлен

```text
1) Переустановить
2) Обновить
3) Удалить
4) Проверить обновления
5) Статус
6) Логи
0) Выход
```

---

## Автоматическая установка

Скрипт пытается определить:

- Docker-контейнер AWG;
- интерфейс (`awg0` и т.д.);
- путь к конфигу внутри контейнера;
- `SERVER_PUBLIC_KEY`;
- внешний endpoint `SERVER_IP`;
- `PUBLIC_HOST`;
- часть AWG-параметров (`Jc`, `Jmin`, `Jmax`, `S1-S4`, `H1-H4`, `I1-I5`).

Обычно руками вводятся только:

- `API_TOKEN`;
- `ADMIN_ID`;
- `SERVER_NAME`.

Если часть данных не определилась автоматически, installer запросит только недостающие значения.

---

## Ручная установка

Ручной режим позволяет явно задать:

- `DOCKER_CONTAINER`;
- `WG_INTERFACE`;
- `SERVER_PUBLIC_KEY`;
- `PUBLIC_HOST`;
- `SERVER_IP`;
- цены в Telegram Stars;
- `DOWNLOAD_URL`;
- `SUPPORT_USERNAME`.

Этот режим полезен, если AWG работает в нестандартном контейнере, под нестандартным интерфейсом или за reverse proxy / нестандартной сетевой схемой.

---

## Где ставится бот

- код проекта: `/opt/amnezia/bot`
- bot code: `/opt/amnezia/bot/bot`
- env: `/opt/amnezia/bot/.env`
- virtualenv: `/opt/amnezia/bot/.venv`
- service: `vpn-bot.service`
- install log: `/var/log/awg-tgbot-install.log`
- app log: `/var/log/awg-tgbot/bot.log`

---

## Повторный запуск меню

```bash
sudo awg-tgbot
```

или

```bash
sudo bash /opt/amnezia/bot/awg-tgbot.sh
```

---

## Полезные команды

### Статус

```bash
systemctl status vpn-bot.service --no-pager -l
```

### Логи сервиса

```bash
journalctl -u vpn-bot.service -f
```

### Логи приложения

```bash
tail -f /var/log/awg-tgbot/bot.log
```

### Проверка обновлений

```bash
sudo awg-tgbot check-updates
```

### Обновление

```bash
sudo awg-tgbot update
```

---

## Переменные окружения

Ниже — основные переменные из `.env`.

### Обязательные

- `API_TOKEN` — токен Telegram-бота;
- `ADMIN_ID` — Telegram user_id администратора;
- `SERVER_PUBLIC_KEY` — публичный ключ сервера AWG;
- `SERVER_IP` — endpoint в формате `IP:port` или `host:port`;
- `ENCRYPTION_SECRET` — ключ для шифрования чувствительных данных в БД.

### Настройки проекта

- `SERVER_NAME` — имя сервера / отображаемое имя VPN;
- `DB_PATH` — путь к SQLite БД;
- `DOWNLOAD_URL` — ссылка на клиент / инструкцию / сайт;
- `SUPPORT_USERNAME` — username поддержки.

### Настройки AWG

- `DOCKER_CONTAINER`
- `WG_INTERFACE`
- `VPN_SUBNET_PREFIX`
- `FIRST_CLIENT_OCTET`
- `MAX_CLIENT_OCTET`
- `PRIMARY_DNS`
- `SECONDARY_DNS`
- `CLIENT_MTU`
- `PERSISTENT_KEEPALIVE`
- `CLIENT_ALLOWED_IPS`
- `AWG_JC`, `AWG_JMIN`, `AWG_JMAX`
- `AWG_S1`, `AWG_S2`, `AWG_S3`, `AWG_S4`
- `AWG_H1`, `AWG_H2`, `AWG_H3`, `AWG_H4`
- `AWG_I1`, `AWG_I2`, `AWG_I3`, `AWG_I4`, `AWG_I5`
- `AWG_PROTOCOL_VERSION`
- `AWG_TRANSPORT_PROTO`

### Коммерческие настройки

- `STARS_PRICE_7_DAYS`
- `STARS_PRICE_30_DAYS`
- `CONFIGS_PER_USER`

### Ограничители / таймауты

- `PURCHASE_CLICK_COOLDOWN_SECONDS`
- `PURCHASE_RATE_LIMIT_TTL_SECONDS`
- `ADMIN_COMMAND_COOLDOWN_SECONDS`
- `DOCKER_RETRIES`
- `DOCKER_RETRY_BASE_DELAY`
- `DOCKER_TIMEOUT_SECONDS`
- `AWG_PEERS_CACHE_TTL_SECONDS`
- `CLEANUP_INTERVAL_SECONDS`
- `IGNORE_PEERS`

---

## Схема данных SQLite

Бот создаёт и поддерживает несколько таблиц:

- `users` — пользователи и срок действия подписки;
- `keys` — устройства пользователя, IP, публичные ключи и зашифрованные секреты;
- `payments` — оплаты и состояние их обработки;
- `audit_log` — журнал действий;
- `pending_actions` — ожидающие подтверждения admin-действия;
- `pending_broadcasts` — отложенная рассылка;
- `protected_peers` — peer, которые нельзя удалять как orphan по ошибке.

---

## Безопасность

- клиентские приватные ключи и PSK хранятся в БД в зашифрованном виде;
- `ENCRYPTION_SECRET` критичен для последующей расшифровки уже сохранённых данных;
- безопасный режим удаления **"Удалить всё, кроме БД и .env"** нужен именно для сохранения возможности расшифровывать существующие записи;
- `/backup` отправляет **редактированную** копию БД без чувствительных значений.

> Потеря `ENCRYPTION_SECRET` означает потерю возможности расшифровывать уже сохранённые клиентские данные.

---

## Работа с orphan peer

Проект учитывает типичную проблему рассинхронизации между AWG и БД:

- бот умеет искать peer, которые есть в AWG, но отсутствуют в БД;
- такие peer считаются orphan;
- есть защита через `protected_peers` и `IGNORE_PEERS`, чтобы не удалить существующие системные / старые peer по ошибке;
- при первом запуске можно bootstrap-нуть существующие peer как защищённые.

---

## Админ-команды

Поддерживаются как минимум следующие команды:

- `/give ID [ДНИ]`
- `/revoke ID`
- `/users`
- `/stats`
- `/orphans`
- `/audit [LIMIT]`
- `/sync_awg`
- `/clean_orphans`
- `/clean_orphans_force`
- `/backup`
- `/send ТЕКСТ`

Также часть действий доступна через inline-кнопки внутри админ-панели.

---

## Удаление

Есть два безопасных режима:

1. `Удалить всё` — удаляет код, БД, `.env`, сервис и логи.
2. `Удалить всё, кроме БД и .env` — удаляет код, сервис, venv и логи, но сохраняет БД и `.env`.

Второй режим полезен, если нужно переустановить бот без потери `ENCRYPTION_SECRET` и без потери возможности расшифровать уже сохранённые секреты.

---

## Разработка и отладка

### Локально на сервере

Обычно проект отлаживается **на том же хосте**, где уже работает AWG-контейнер, потому что backend опирается на:

- `docker exec` в реальный контейнер;
- чтение конфигурации AWG;
- наличие корректного `SERVER_PUBLIC_KEY` и `SERVER_IP`.

### Запуск bot-кода без installer

```bash
cd bot
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# заполнить .env
python app.py
```

> Для полноценной работы нужен реальный AWG-контейнер, доступный через Docker.

---

## Codespaces

**Codespaces подходит для:**

- ревью кода;
- правок `README.md`;
- правок UI-текстов;
- проверки структуры проекта;
- подготовки PR.

**Codespaces не подходит как полноценная production-среда по умолчанию**, потому что проект завязан на `systemd`, root-установку, `docker exec` и уже работающий контейнер AWG.

### Создать Codespace через GitHub CLI

```bash
gh codespace create -R Just1k13/awg-tgbot -b main
```

Либо открыть репозиторий на GitHub → **Code** → **Codespaces** → **Create codespace on main**.

### Что делать внутри Codespace

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r bot/requirements.txt
cp bot/.env.example bot/.env
```

После этого можно:

- редактировать README;
- смотреть код;
- готовить PR;
- частично запускать код, если подложить совместимое окружение и mock / доступ к нужному Docker runtime.

### Быстро заменить README внутри Codespace

```bash
curl -L -o README.md <PUT-YOUR-RAW-README-URL-HERE>
```

или просто вставить новый текст вручную в `README.md`.

---

## Ограничения проекта

- нет встроенного развёртывания самого AWG — ожидается уже готовый сервер;
- нет штатного devcontainer / `.devcontainer` конфигуратора;
- нет тестов и CI внутри репозитория;
- сервис запускается от `root`, потому что проект тесно интегрирован с Docker и системным окружением сервера.

---

## Важно

- `API_TOKEN` и `ADMIN_ID` не подставляются автоматически — их нужно вводить вручную;
- если бот отвечает `Unauthorized`, перевыпусти токен в BotFather и обнови `.env`;
- `ENCRYPTION_SECRET` должен быть сохранён и защищён;
- при переносе БД на новый сервер без старого `ENCRYPTION_SECRET` расшифровка секретов не сработает;
- installer ждёт освобождения `apt/dpkg lock`, если пакетный менеджер занят.

---

## Что стоит добавить в будущем

- `.devcontainer/devcontainer.json` для нормального Codespaces workflow;
- `LICENSE`;
- `CHANGELOG.md`;
- pinned-версии зависимостей;
- smoke tests / unit tests;
- GitHub Actions для lint / test / release;
- отдельный режим mock/dev без реального AWG-контейнера.

