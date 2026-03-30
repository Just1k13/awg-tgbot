# RKN / state-services denylist baseline (operator template)

> Важно: это **операторский шаблон**, не юридическая консультация.  
> Список нужно валидировать и поддерживать вручную под вашу юрисдикцию и риски.

## Цель

- Снизить риск претензий к VPN-доступу за счёт блокировки доступа к государственным сервисам и связанным доменам через VPN-туннель.
- Реализуется через `EGRESS_DENYLIST_DOMAINS` + `EGRESS_DENYLIST_MODE=strict`.

## Обновлённый baseline (расширенный)

```text
gosuslugi.ru
www.gosuslugi.ru
esia.gosuslugi.ru
pos.gosuslugi.ru
gosweb.gosuslugi.ru
госуслуги.рф

rkn.gov.ru
pd.rkn.gov.ru
service.rkn.gov.ru

nalog.gov.ru
www.nalog.gov.ru
lkfl2.nalog.ru

fssp.gov.ru
www.fssp.gov.ru

mvd.ru
www.mvd.ru
мвд.рф

gibdd.ru
www.gibdd.ru

sfr.gov.ru
pfr.gov.ru

rosreestr.gov.ru
www.rosreestr.gov.ru

zakupki.gov.ru
pravo.gov.ru
```

## Дополнительно: приложение/сайт MAX

Если вы имели в виду именно сервис **MAX (max.ru)**, добавьте его домены в основной denylist:

```text
max.ru
www.max.ru
```


## Рекомендованный `.env` фрагмент

```dotenv
EGRESS_DENYLIST_ENABLED=1
EGRESS_DENYLIST_MODE=strict
EGRESS_DENYLIST_DOMAINS=gosuslugi.ru,www.gosuslugi.ru,esia.gosuslugi.ru,pos.gosuslugi.ru,gosweb.gosuslugi.ru,госуслуги.рф,rkn.gov.ru,pd.rkn.gov.ru,service.rkn.gov.ru,nalog.gov.ru,www.nalog.gov.ru,lkfl2.nalog.ru,fssp.gov.ru,www.fssp.gov.ru,mvd.ru,www.mvd.ru,мвд.рф,gibdd.ru,www.gibdd.ru,sfr.gov.ru,pfr.gov.ru,rosreestr.gov.ru,www.rosreestr.gov.ru,zakupki.gov.ru,pravo.gov.ru,max.ru,www.max.ru
```

## Готовый пресет в проекте

Начиная с текущей версии, baseline домены уже зашиты в дефолты:
- `EGRESS_DENYLIST_DOMAINS` = список из блока выше;
- `EGRESS_DENYLIST_MODE` = `strict`;
- включение делается одним флагом: `EGRESS_DENYLIST_ENABLED=1`.

## Откуда взяты домены (проверка в интернете)

- Госуслуги / ЕСИА: `gosuslugi.ru`, `esia.gosuslugi.ru`.
- Роскомнадзор: `rkn.gov.ru`, портал ПДн `pd.rkn.gov.ru`, сервисы `service.rkn.gov.ru`.
- ФНС: `nalog.gov.ru`.

(Дальше список расширен по официальным доменным зонам гос-сервисов и практической эксплуатации denylist.
Отдельно добавлен пример для сервиса MAX: `max.ru`.)

## Операционные правила

1. Доступ через VPN к государственным сайтам/приложениям и сервисам РКН запрещён.
2. При каждом релизе обновляйте список доменов.
3. После изменения `.env` запускайте:
   - `sudo awg-tgbot sync-helper-policy`
   - `sudo awg-tgbot status`
4. Проверяйте метрики denylist в админке/логах после первого sync, чтобы поймать ложные блокировки.
