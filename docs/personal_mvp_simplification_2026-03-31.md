# Simplification audit (2026-03-31)

## Core MVP: оставить

- Telegram Stars платежи + продление.
- Выдача `vpn://` и `.conf`.
- Минимальный user flow (`/start`, buy/renew, status, configs, guide, support).
- Минимальный admin flow (users, give/extend/revoke, stats, broadcast).
- Рефералы (one code + first successful payment trigger).
- Автоотключение истёкших пользователей.
- systemd + отдельный системный пользователь + helper boundary.
- Статический denylist.

## Оставить, но упростить

- Installer: оставить install/reinstall/status/remove/diagnostics, убрать platform-like update UX.
- Admin surface: убрать тяжёлые operator-пункты из основного меню.
- Docs: сфокусировать на personal self-hosted сценарии.

## Убрать/отключить в интерфейсе

- Branch switching (main/beta).
- Pinned update path (`check-updates`, `REPO_UPDATE_REF`, update-menu).
- Текстовый/настроечный editor как основной путь в админке.
- Orphan cleanup и health/qos-heavy surface из админ-меню.

## Принятые компромиссы

- Бэкенд-код «продвинутых» функций оставлен в проекте для обратной совместимости данных и постепенного дальнейшего упрощения.
- В рамках этого pass отключён/сужен именно основной operational surface (installer + admin UI + документация).
