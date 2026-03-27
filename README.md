# awg-tgbot

Telegram-бот для продажи доступа к **уже работающему** AmneziaWG (AWG) серверу в self-hosted формате.

> [!IMPORTANT]
> Этот файл описывает **стабильную ветку `main`**.  
> Если вы читаете репозиторий в ветке `beta`, откройте [`README.beta.md`](README.beta.md) — там отдельный onboarding и акценты для beta-сценария.

> `awg-tgbot` не поднимает AWG с нуля.  
> Он автоматизирует продажу доступа, выдачу конфигов и администрирование поверх существующего AWG-контейнера.

## Кому подходит

- владельцам небольших self-hosted VPN-проектов;
- администраторам, которым нужен простой Telegram-канал продаж через Stars;
- тем, кто хочет управлять пользователями без внешних SaaS-панелей.

## Что важно знать сразу

- Рекомендуемая ветка для продакшена: **`main`**.
- Для установки нужен **Ubuntu/Debian + Docker + root + systemd**.
- Проект работает в модели **single-node + SQLite**.
- Секреты в БД шифруются, но безопасность критично зависит от `ENCRYPTION_SECRET`.

---

## Содержание

- [Быстрый старт (main)](#быстрый-старт-main)
- [Ветки main и beta](#ветки-main-и-beta)
- [Что умеет проект](#что-умеет-проект)
- [Как это работает](#как-это-работает)
- [Установка, обновление, удаление](#установка-обновление-удаление)
- [Операционка: статус, логи, проверка](#операционка-статус-логи-проверка)
- [Конфигурация (.env)](#конфигурация-env)
- [Безопасность и данные](#безопасность-и-данные)
- [Архитектура репозитория](#архитектура-репозитория)
- [Ограничения](#ограничения)
- [Troubleshooting / FAQ](#troubleshooting--faq)
- [Для разработчика](#для-разработчика)
- [Roadmap](#roadmap)

---

## Быстрый старт (main)

### 1) Предварительные требования

Подготовьте сервер:

- Ubuntu/Debian;
- root-доступ;
- рабочий Docker;
- уже работающий контейнер AmneziaWG/AWG;
- токен Telegram-бота;
- `Telegram user_id` администратора.

### 2) Установка из `main`

```bash
curl -fsSL https://raw.githubusercontent.com/Just1k13/awg-tgbot/main/awg-tgbot.sh | sudo bash
```

или

```bash
wget -qO- https://raw.githubusercontent.com/Just1k13/awg-tgbot/main/awg-tgbot.sh | sudo bash
```

Скрипт откроет интерактивное меню и проведёт установку/настройку.

### 3) Быстрая проверка после установки

```bash
sudo systemctl status vpn-bot.service --no-pager
sudo journalctl -u vpn-bot.service -n 100 --no-pager
```

В Telegram проверьте:

1. `/start` у бота;
2. открывается меню;
3. в профиле корректно отображается статус;
4. тестовая покупка/выдача проходит без ошибок.

---

## Ветки `main` и `beta`

| Ветка | Назначение | Кому выбирать |
|---|---|---|
| `main` | Стабильный основной сценарий | Большинству self-hosted установок |
| `beta` | Площадка для новых/усиленных механизмов надёжности | Тем, кто готов тестировать изменения раньше |

Первая установка `beta`:

```bash
curl -fsSL https://raw.githubusercontent.com/Just1k13/awg-tgbot/beta/awg-tgbot.sh | sudo REPO_BRANCH=beta bash
```

После установки выбранная ветка сохраняется в `/opt/amnezia/bot/.state/repo_branch`.

Переключение ветки на установленной системе:

```bash
sudo REPO_BRANCH=main awg-tgbot update
sudo REPO_BRANCH=beta awg-tgbot update
```

---

## Что умеет проект

### Пользователь

- покупка подписки за Telegram Stars;
- продление активной подписки;
- просмотр срока действия;
- получение до `CONFIGS_PER_USER` конфигов;
- выдача `vpn://` ключа и `.conf` файла;
- инструкция и контакт поддержки.

### Администратор

- админ-панель внутри бота;
- статистика по пользователям/ключам/слотам;
- ручная выдача доступа;
- отзыв доступа и полное удаление пользователя;
- проверка синхронизации AWG ↔ SQLite;
- очистка orphan peers;
- аудит действий;
- рассылка;
- backup БД без секретов.

### Сервер / инфраструктура

- единый скрипт установки/обновления/удаления/обслуживания;
- systemd-сервис `vpn-bot.service`;
- автоопределение AWG-параметров (где возможно);
- SQLite с миграциями и audit trail.

---

## Как это работает

1. Пользователь выбирает тариф в Telegram.
2. Оплачивает через Telegram Stars.
3. Бот фиксирует платёж в SQLite и обрабатывает его.
4. Подписка активируется/продлевается.
5. Для пользователя создаются (или переиспользуются) конфиги в пределах `CONFIGS_PER_USER`.
6. Пользователь получает `vpn://` и `.conf`.
7. Фоновая задача чистит просроченные подписки и удаляет peer из AWG.

---

## Установка, обновление, удаление

### Повторный запуск меню

```bash
sudo awg-tgbot
```

### Обновление

```bash
sudo awg-tgbot update
```

### Проверка обновлений

```bash
sudo awg-tgbot check-updates
```

### Статус и логи через helper

```bash
sudo awg-tgbot status
sudo awg-tgbot logs
```

### Удаление

```bash
sudo awg-tgbot remove
```

> В интерактивном режиме доступны безопасные варианты удаления (включая сценарий оставить `.env` и БД).

---

## Операционка: статус, логи, проверка

### Основные пути

- проект: `/opt/amnezia/bot`
- код бота: `/opt/amnezia/bot/bot`
- конфиг: `/opt/amnezia/bot/.env`
- virtualenv: `/opt/amnezia/bot/.venv`
- service: `/etc/systemd/system/vpn-bot.service`
- install log: `/var/log/awg-tgbot-install.log`
- app log: `/var/log/awg-tgbot/bot.log`
- выбранная ветка: `/opt/amnezia/bot/.state/repo_branch`

### Полезные команды

```bash
sudo systemctl restart vpn-bot.service
sudo systemctl status vpn-bot.service --no-pager
sudo journalctl -u vpn-bot.service -f
sudo tail -f /var/log/awg-tgbot/bot.log
```

---

## Конфигурация (.env)

Ниже — практический минимум.

### Обязательные

- `API_TOKEN` — токен Telegram-бота;
- `ADMIN_ID` — Telegram user ID администратора;
- `ENCRYPTION_SECRET` — ключевой секрет для шифрования чувствительных данных.

### Критичные для AWG-интеграции

- `DOCKER_CONTAINER`
- `WG_INTERFACE`
- `SERVER_PUBLIC_KEY`
- `SERVER_IP` (endpoint `host:port`)

### Коммерческие настройки

- `STARS_PRICE_7_DAYS`
- `STARS_PRICE_30_DAYS`
- `CONFIGS_PER_USER`

### Сетевые ограничения/пул

- `VPN_SUBNET_PREFIX`
- `FIRST_CLIENT_OCTET`
- `MAX_CLIENT_OCTET`

> Изменяйте границы пула осторожно: это напрямую влияет на доступную ёмкость и конфликт IP.

---

## Безопасность и данные

### Что хранится в БД

- пользователи и срок подписки;
- выданные ключи/peer-метаданные;
- платежи и статусы обработки;
- audit log;
- служебные состояния админ-действий.

### Шифрование

Чувствительные данные (клиентские приватные/PSK и т.п.) хранятся зашифрованно.

### Почему `ENCRYPTION_SECRET` критичен

- потеря секрета = невозможность корректно расшифровать ранее сохранённые секреты;
- смена секрета на уже работающей инсталляции без миграции приведёт к проблемам с существующими данными.

### Backup

Через админ-команду backup формируется копия БД с редактированными (очищенными) секретами.

### Рискованные действия

- ручное редактирование SQLite без понимания схемы;
- перенос БД между инсталляциями с разными `ENCRYPTION_SECRET`;
- агрессивная ручная очистка peer в AWG мимо бота.

---

## Архитектура репозитория

```text
awg-tgbot/
├─ awg-tgbot.sh                # installer/updater/remover/maintenance
├─ README.md                   # документация для main
├─ README.beta.md              # документация для beta
└─ bot/
   ├─ app.py                   # entrypoint, polling, startup checks
   ├─ payments.py              # платежи Stars и обработка выдачи
   ├─ handlers_user.py         # пользовательские команды/кнопки
   ├─ handlers_admin.py        # админ-команды и инструменты
   ├─ awg_backend.py           # интеграция с AWG через docker exec
   ├─ database.py              # SQLite-схема, миграции, CRUD
   ├─ config.py                # загрузка/валидация .env
   ├─ security_utils.py        # шифрование/дешифрование секретов
   ├─ helpers.py               # утилиты
   ├─ keyboards.py             # клавиатуры Telegram
   ├─ texts.py                 # текстовые шаблоны
   ├─ ui_constants.py          # callback/button constants
   └─ requirements.txt
```

---

## Ограничения

- не разворачивает AmneziaWG с нуля;
- ориентирован на single-node сценарий;
- SQLite не рассчитан на multi-writer/high-concurrency кластер;
- зависит от доступности Docker и AWG-интерфейса в контейнере;
- не является биллинговой платформой общего назначения.

---

## Troubleshooting / FAQ

### Telegram API вернул `Unauthorized`

Проверьте `API_TOKEN` в `.env`, при необходимости перевыпустите токен через BotFather и перезапустите сервис.

### Сервис не стартует

```bash
sudo systemctl status vpn-bot.service --no-pager
sudo journalctl -u vpn-bot.service -n 200 --no-pager
```

Проверьте `.env`, доступность Docker и AWG-контейнера.

### Docker/AWG недоступен

- `docker ps` должен работать без ошибок;
- контейнер, указанный в `DOCKER_CONTAINER`, должен существовать;
- интерфейс `WG_INTERFACE` должен быть виден в `awg show` внутри контейнера.

### Проблемы после переноса БД

Сверьте:

- `ENCRYPTION_SECRET` (должен совпадать);
- сетевые параметры (`VPN_SUBNET_PREFIX`, диапазон octet);
- актуальность ключей в AWG и записей в SQLite.

---

## Для разработчика

### Локальный запуск (без installer)

1. Подготовьте Python окружение и зависимости из `bot/requirements.txt`.
2. Создайте рабочий `.env`.
3. Запустите `bot/app.py`.

### Про Codespaces / dev-среды

Разработка кода и ревью в Codespaces возможны, но:

- полноценная интеграция с AWG обычно недоступна без реального Docker+AWG окружения;
- платежный и provisioning flow корректно проверяются только на сервере с рабочим AWG.

---

## Roadmap

Потенциальные улучшения (без обещаний сроков):

- улучшение интеграционных проверок перед обновлением;
- дополнительные безопасные инструменты диагностики AWG ↔ SQLite;
- развитие документации по миграциям и recovery-сценариям.

---

Если вы используете `beta` или хотите тестировать новые механизмы надёжности — см. [`README.beta.md`](README.beta.md).
