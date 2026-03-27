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
  - просмотра логов,
  - безопасной предварительной проверки окружения;
- автоопределение контейнера AWG, интерфейса, `SERVER_PUBLIC_KEY`, внешнего `SERVER_IP` и AWG-параметров;
- отдельный read-only этап `preflight` / `detect_install_state` перед интерактивным меню;
- обычный запуск `bash awg-tgbot.sh` или `sudo awg-tgbot` сначала показывает сводку проверки, затем меню действий;
- установка **не стартует автоматически** и запускается только по явному выбору пользователя;
- создание и обновление `systemd`-сервиса;
- запуск сервиса под отдельным системным пользователем `awg-bot` (least-privilege);
- AWG операции выполняются через ограниченный root-helper с allowlist команд (без добавления бота в `docker` group);
- helper использует policy-файл `/etc/awg-bot-helper.json` как целевой allowlist для `container/interface`;
- хранение локального SHA установленной версии для проверки обновлений;
- crash-safe обработка платежей со статусами `received/provisioning/applied/failed/needs_repair`;
- recovery worker для повторной выдачи доступа после сбоев;
- quarantine-подход для orphan peer (без агрессивного удаления по умолчанию).

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
4. Фоновые worker-ы периодически очищают просроченные подписки, stale pending-reservation и зависшие платежи.
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
- Python >= 3.10;
- установленный Docker;
- уже поднятый и рабочий контейнер AmneziaWG / AWG;
- токен Telegram-бота;
- `Telegram user_id` администратора.

---

## Быстрый запуск

### Основная ветка (`main`)

```bash
set -euo pipefail
curl -fsSL https://raw.githubusercontent.com/Just1k13/awg-tgbot/main/awg-tgbot.sh | sudo bash -s --
```

или

```bash
set -euo pipefail
wget -qO- https://raw.githubusercontent.com/Just1k13/awg-tgbot/main/awg-tgbot.sh | sudo bash -s --
```

> Почему добавлен `set -euo pipefail`: если `curl/wget` не смог скачать скрипт, без `pipefail` `bash` может завершиться с `0` на пустом stdin и это выглядит как «silent no-op». С `pipefail` команда падает явно.

### Бета-ветка (`beta`)

Для первичной установки `beta` используй запуск с переменной `REPO_BRANCH=beta`:

```bash
set -euo pipefail
curl -fsSL https://raw.githubusercontent.com/Just1k13/awg-tgbot/beta/awg-tgbot.sh | sudo REPO_BRANCH=beta bash -s --
```

или

```bash
set -euo pipefail
wget -qO- https://raw.githubusercontent.com/Just1k13/awg-tgbot/beta/awg-tgbot.sh | sudo REPO_BRANCH=beta bash -s --
```

После первой установки ветка сохраняется автоматически в `/opt/amnezia/bot/.state/repo_branch`, поэтому дальнейшие обновления уже можно запускать обычной командой:

```bash
sudo awg-tgbot update
```

### Как переключить уже установленного бота между `beta` и `main`

Перейти с `beta` на `main`:

```bash
sudo REPO_BRANCH=main awg-tgbot update
```

Вернуться с `main` на `beta`:

```bash
sudo REPO_BRANCH=beta awg-tgbot update
```

После такого обновления новая ветка тоже сохранится автоматически.

---

## Как теперь работает обычный запуск

Обычный интерактивный запуск без аргументов:

```bash
sudo awg-tgbot
```

или

```bash
sudo bash /opt/amnezia/bot/awg-tgbot.sh
```

теперь всегда делает одно и то же:

1. выполняет безопасную read-only проверку состояния системы;
2. печатает краткую сводку;
3. открывает меню действий по найденному состоянию;
4. запускает установку **только после явного выбора** пользователя.

При старте скрипт **не должен**:

- ставить пакеты;
- создавать файлы;
- менять `.env`;
- менять `systemd`;
- запускать `install_or_reinstall_flow install` автоматически.

---

## Предварительная проверка (`preflight`)

На старте выполняется отдельный read-only этап `preflight` / `detect_install_state`.

Он проверяет:

### AWG

- установлен ли Docker CLI;
- доступен ли Docker daemon;
- найден ли рабочий контейнер AmneziaWG / AWG;
- определяется ли AWG интерфейс;
- определяется ли AWG config.

### Бот / installer

- существует ли `SERVICE_FILE`;
- существует ли `BOT_DIR`;
- существует ли `BOT_DIR/app.py`;
- существует ли symlink `/usr/local/bin/awg-tgbot`;
- есть ли `.env`;
- есть ли служебное состояние установки (`/opt/amnezia/bot/.state`).

### Что выводится в startup summary

Перед меню показывается сводка вида:

```text
AWG: найден / не найден
Бот: установлен / не установлен
Ветка: текущая
Service: найден / не найден
Docker: доступен / недоступен
```

Дополнительно выводятся более детальные поля:

- `Docker CLI: установлен / не установлен`
- `Docker daemon: доступен / недоступен`
- `AWG контейнер: найден / не найден`
- `AWG интерфейс: найден / не найден`
- `AWG config: найден / не найден`
- `BOT_DIR: найден / не найден`
- `BOT_DIR/app.py: найден / не найден`
- `Symlink /usr/local/bin/awg-tgbot: найден / не найден`
- `.env: найден / не найден`
- `Служебное состояние установки: найдено / не найдено`

Проверку можно вызвать отдельно:

```bash
sudo awg-tgbot preflight
```

или

```bash
sudo awg-tgbot detect-install-state
```

---

## Логика состояний при старте

### 1) AWG найден, бот не установлен

Скрипт показывает состояние:

```text
AWG найден, бот не установлен.
```

Меню:

```text
1) Установить
2) Выбор ветки
3) Повторить проверку
0) Выход
```

### 2) AWG найден, бот установлен

Скрипт показывает состояние:

```text
AWG найден, бот установлен.
```

Меню:

```text
1) Статус
2) Логи
3) Проверить обновления
4) Обновить
5) Переустановить
6) Удалить
7) Выбор ветки
8) Повторить проверку
0) Выход
```

### 3) AWG не найден, бот установлен

Скрипт показывает предупреждение:

```text
Установка бота найдена, но AWG сейчас не обнаружен.
```

Меню:

```text
1) Статус
2) Логи
3) Переустановить
4) Удалить
5) Выбор ветки
6) Повторить проверку
0) Выход
```

### 4) AWG не найден, бот не установлен

Скрипт показывает понятное сообщение:

```text
AWG не найден и бот не установлен.
Сначала установи и запусти AWG, затем вернись к установке бота.
```

Меню:

```text
1) Выбор ветки
2) Повторить проверку
0) Выход
```

> В этом состоянии установка не запускается и пункт `Установить` не показывается.

---

## Режимы работы installer

> Важно по safety: без TTY интерактивное меню и любые prompt-зависимые сценарии завершаются с понятной ошибкой (safe-fail). Для non-interactive запуска используй только явные action-команды (`status`, `check-updates`, `update`, `preflight`, `sync-helper-policy`), которые не требуют ввода.

### Non-interactive action-команды

```bash
sudo awg-tgbot preflight
sudo awg-tgbot status
sudo awg-tgbot check-updates
sudo awg-tgbot update
sudo awg-tgbot sync-helper-policy
```

Дополнительно доступны:

```bash
sudo awg-tgbot install
sudo awg-tgbot reinstall
sudo awg-tgbot logs
sudo awg-tgbot remove
```

Но есть важное ограничение:

- `install` не стартует, если AWG не обнаружен;
- destructive сценарии (`remove-default`, `remove-full`) требуют явного подтверждения с реальным вводом и безопасно отказываются без TTY;
- `remove-default` не подтверждается «молчаливым default».

---

## Автоматическая установка

Установка запускается только после явного выбора `Установить` в меню или через явный action `install`.

Перед самой установкой скрипт снова проверяет состояние. Если AWG не обнаружен, установка останавливается с понятной ошибкой.

Скрипт пытается определить:

- Docker-контейнер AWG;
- интерфейс (`awg0` и т.д.);
- путь к конфигу внутри контейнера;
- `SERVER_PUBLIC_KEY`;
- внешний endpoint `SERVER_IP` в формате `IPv4:port`;
- `PUBLIC_HOST` как внешний IPv4 без порта;
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

Этот режим полезен, если AWG работает в нестандартном контейнере, под нестандартным интерфейсом или если внешний IPv4 / порт нужно задать вручную.

## Консистентность `.env` и helper policy

- Runtime использует `DOCKER_CONTAINER`/`WG_INTERFACE` из `.env`.
- Root-helper дополнительно ограничен policy-файлом `/etc/awg-bot-helper.json`.
- Installer при установке/обновлении синхронизирует policy из `.env` и валидирует значения заранее.
- `sudo awg-tgbot status` показывает target из `.env` и из policy, а при рассинхроне выдаёт явное предупреждение.

Если ты вручную поменял `DOCKER_CONTAINER` или `WG_INTERFACE` в `.env`, обязательно синхронизируй policy:

```bash
sudo awg-tgbot sync-helper-policy
sudo awg-tgbot status
```

---

## Где ставится бот

- код проекта: `/opt/amnezia/bot`
- bot code: `/opt/amnezia/bot/bot`
- env: `/opt/amnezia/bot/.env`
- virtualenv: `/opt/amnezia/bot/.venv`
- service: `vpn-bot.service`
- install log: `/var/log/awg-tgbot-install.log`
- app log: `/var/log/awg-tgbot/bot.log`
- выбранная ветка: `/opt/amnezia/bot/.state/repo_branch`

---

## Полезные команды

### Проверка состояния перед любыми действиями

```bash
sudo awg-tgbot preflight
```

### Статус через installer

```bash
sudo awg-tgbot status
```

### Логи через installer

```bash
sudo awg-tgbot logs
```

### Статус systemd

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

### Обновление текущей установленной ветки

```bash
sudo awg-tgbot update
```

---

## Быстрая проверка после установки

```bash
sudo awg-tgbot preflight
sudo awg-tgbot status
systemctl status vpn-bot.service --no-pager -l
journalctl -u vpn-bot.service -n 50 --no-pager
grep -E '^(SERVER_NAME|SERVER_IP|SERVER_PUBLIC_KEY|PUBLIC_HOST)=' /opt/amnezia/bot/.env
```

Что стоит увидеть:

- в `preflight` AWG определяется как найденный;
- сервис `vpn-bot.service` в состоянии `active (running)`;
- в логах есть строки про успешный запуск polling;
- в `.env` заполнены `SERVER_NAME`, `SERVER_IP`, `SERVER_PUBLIC_KEY`;
- в статусе installer показывается правильная текущая ветка.

---

## Переменные окружения

Ниже — основные переменные из `.env`.

### Обязательные

- `API_TOKEN` — токен Telegram-бота;
- `ADMIN_ID` — Telegram user_id администратора;
- `SERVER_PUBLIC_KEY` — публичный ключ сервера AWG;
- `SERVER_IP` — endpoint в формате `IPv4:port`;
- `ENCRYPTION_SECRET` — ключ для шифрования чувствительных данных в БД.

### Настройки проекта

- `SERVER_NAME` — только отображаемое имя сервера / VPN в клиенте;
- `DB_PATH` — путь к SQLite БД;
- `DOWNLOAD_URL` — ссылка на клиент / инструкцию / сайт;
- `SUPPORT_USERNAME` — username поддержки;
- `PUBLIC_HOST` — внешний IPv4 без порта, используется для автосборки `SERVER_IP`.

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
- `AWG_HELPER_PATH`
- `AWG_HELPER_USE_SUDO`

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
- `ENCRYPTION_PBKDF2_ITERATIONS`

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

Есть два режима удаления:

1. `Удалить (сохранить БД и .env)` — удаляет код, сервис, venv и логи, но сохраняет пользовательские данные (`.env` и БД).
2. `Полностью удалить` — удаляет всё, включая БД и `.env`.

Для полного удаления требуется явное подтверждение вводом слова `DELETE`.
⚠️ При любом режиме удаления peer внутри AWG-контейнера автоматически не удаляются.

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
gh codespace create -R Just1k13/awg-tgbot -b beta
```

Либо открыть репозиторий на GitHub → **Code** → **Codespaces** → **Create codespace on beta**.

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

---

## Ограничения проекта

- нет встроенного развёртывания самого AWG — ожидается уже готовый сервер;
- нет штатного devcontainer / `.devcontainer` конфигуратора;
- есть базовые автотесты (`tests/test_critical_flows.py`), но покрытия недостаточно;
- CI пока не настроен;
- сервис запускается от системного пользователя `awg-bot`, но установка/обновление требуют root.

---

## Важно

- `API_TOKEN` и `ADMIN_ID` не подставляются автоматически — их нужно вводить вручную;
- для этого проекта endpoint должен быть только по внешнему IPv4; домены и hostname как endpoint не используются;
- `SERVER_NAME` не участвует в сборке endpoint и нужен только как отображаемое имя VPN;
- если бот отвечает `Unauthorized`, перевыпусти токен в BotFather и обнови `.env`;
- `ENCRYPTION_SECRET` должен быть сохранён и защищён;
- шифрование новых значений использует PBKDF2 (scheme `enc:v2`), старые `enc:v1` остаются читаемыми;
- при переносе БД на новый сервер без старого `ENCRYPTION_SECRET` расшифровка секретов не сработает;
- installer ждёт освобождения `apt/dpkg lock`, если пакетный менеджер занят;
- обычный запуск без аргументов сначала делает `preflight` и не стартует установку автоматически.

---

## Что стоит добавить в будущем

- `.devcontainer/devcontainer.json` для нормального Codespaces workflow;
- `LICENSE`;
- `CHANGELOG.md`;
- pinned-версии зависимостей;
- smoke tests / unit tests;
- GitHub Actions для lint / test / release;
- отдельный режим mock/dev без реального AWG-контейнера.

---

## Дополнительная документация

- `docs/security.md` — модель привилегий и схема шифрования.
- `docs/migration.md` — шаги обновления и проверки после миграции.
