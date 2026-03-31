# awg-tgbot (personal self-hosted MVP)

`awg-tgbot` — это **личный self-hosted Telegram-бот** для продажи/выдачи доступа к уже работающему AmneziaWG (AWG).

Проект теперь намеренно упрощён под сценарий одного владельца:

1. скачал installer;
2. ввёл токен/админа;
3. запустил;
4. скинул друзьям ссылку на бота;
5. бот сам принимает оплату, выдаёт/продлевает доступ и отключает истёкшие.

## Что входит в MVP

### User flow
- `/start`;
- покупка и продление через Telegram Stars;
- статус подписки;
- получение `vpn://` и `.conf`;
- инструкция и контакт поддержки;
- простой referral entry через deep-link.

### Admin flow
- список пользователей и карточка пользователя;
- выдать/продлить доступ;
- отключить доступ;
- базовая статистика;
- реферальная сводка;
- массовая рассылка с подтверждением.

### Ops / infra
- one-command installer;
- install / reinstall / status / remove / preflight / diagnostics;
- запуск от отдельного системного пользователя;
- helper boundary для операций AWG;
- автозапуск через systemd;
- фоновые задачи на автоотключение истёкших и восстановление зависших операций;
- статический denylist (без сложной панели управления).

## Что намеренно НЕ поддерживается в personal MVP

- переключение `main/beta` ветки из installer;
- pinned update flow с SHA и сложным update-оркестратором;
- «editable platform» с UI-редактором текстов/настроек как основной админ-путь;
- QoS/speed-limit operator flow;
- force/orphan cleanup operator-подсистема;
- перегруженная diagnostics/ops-обвязка в пользовательском интерфейсе.

> Идея: меньше ручек, меньше флагов, меньше хрупкости.

## Быстрый старт

```bash
set -euo pipefail
curl -fsSL https://raw.githubusercontent.com/Just1k13/awg-tgbot/selfhost/awg-tgbot.sh | sudo bash -s --
```

После установки проверьте:

```bash
sudo awg-tgbot preflight
sudo awg-tgbot status
systemctl status vpn-bot.service --no-pager -l
```

## Основные команды installer

```bash
sudo awg-tgbot            # интерактивное меню
sudo awg-tgbot install
sudo awg-tgbot reinstall
sudo awg-tgbot status
sudo awg-tgbot diagnostics
sudo awg-tgbot preflight
sudo awg-tgbot logs
sudo awg-tgbot remove
```

## Рефералы (упрощённая логика)

- у пользователя один referral code;
- один deep-link вида `/start ref_...`;
- бонус начисляется только после первой успешной оплаты приглашённого;
- без сложной сегментации и «маркетинговой платформы».

## Broadcast (операторский emergency-режим)

- команда подготовки сообщения;
- обязательное подтверждение перед отправкой;
- без шаблонов, сегментов и тяжёлой аналитики.

## Denylist

Оставлен статический denylist-подход для снижения риска нежелательных направлений использования VPN.

- один preset/list;
- без тяжёлой policy-platform панели;
- фокус на практичную безопасность с минимальной поддержкой.

## Требования

- Ubuntu / Debian;
- root-доступ;
- Docker;
- уже установленный и рабочий контейнер AmneziaWG/AWG;
- Telegram bot token;
- Telegram user_id администратора.

## Лицензия

MIT.
