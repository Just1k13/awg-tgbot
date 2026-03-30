# RKN / state-services denylist baseline (operator template)

> Важно: это **операторский шаблон**, не юридическая консультация.  
> Список нужно валидировать и поддерживать вручную под вашу юрисдикцию и риски.

## Цель

- Снизить риск претензий к VPN-доступу за счёт блокировки доступа к государственным сервисам и связанным доменам через VPN-туннель.
- Реализуется через `EGRESS_DENYLIST_DOMAINS` + `EGRESS_DENYLIST_MODE=strict`.

## Стартовый список доменов

```text
gosuslugi.ru
www.gosuslugi.ru
esia.gosuslugi.ru
госуслуги.рф
roskomnadzor.gov.ru
rkn.gov.ru
nalog.gov.ru
fssp.gov.ru
mvd.ru
```

## Рекомендованный `.env` фрагмент

```dotenv
EGRESS_DENYLIST_ENABLED=1
EGRESS_DENYLIST_MODE=strict
EGRESS_DENYLIST_DOMAINS=gosuslugi.ru,www.gosuslugi.ru,esia.gosuslugi.ru,госуслуги.рф,roskomnadzor.gov.ru,rkn.gov.ru,nalog.gov.ru,fssp.gov.ru,mvd.ru
```

## Операционные правила

1. Доступ через VPN к государственным сайтам/приложениям и сервисам РКН запрещён.
2. При каждом релизе обновляйте список доменов.
3. После изменения `.env` запускайте:
   - `sudo awg-tgbot sync-helper-policy`
   - `sudo awg-tgbot status`
