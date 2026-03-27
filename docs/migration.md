# Migration notes

## 1) Privilege model migration

After update, installer will:

1. install helper `/usr/local/libexec/awg-bot-helper` (root-owned),
2. write root-owned helper policy `/etc/awg-bot-helper.json` with fixed `container/interface`,
2. create sudoers file `/etc/sudoers.d/awg-bot-helper`,
3. force-remove runtime user `awg-bot` from `docker` group (install/update/reinstall),
4. stop using direct `docker` group membership for runtime bot user.

Migration off `docker` group is idempotent: if user is already removed (or group does not exist), installer logs that migration is already complete.

## 2) Encryption migration

- Existing encrypted records (`enc:v1`) continue to decrypt normally.
- New/updated records are stored as `enc:v2` automatically.
- No DB schema migration required for encryption change.

## 3) Delete flow migration

- `delete_user_everywhere()` now treats `delete_pending` as retryable state.
- User row is not deleted while peer deletion is still pending.

## 4) Manual checks after update

1. `sudo awg-tgbot status`
2. `id awg-bot` → output must not include `docker`
2. `systemctl status vpn-bot.service --no-pager -l`
3. `journalctl -u vpn-bot.service -n 100 --no-pager`
4. `sudo cat /etc/awg-bot-helper.json` and verify `container/interface` match your AWG runtime.
5. In app logs verify helper operations are successful and no `sudo` permission errors.

## 5) Revoke/delete reliability changes

- `revoke_user_access()` now uses `revoke_pending` retryable state and will **not** set `users.sub_until='0'` while at least one AWG peer is still present.
- `delete_user_everywhere()` keeps `delete_pending` semantics and remains retry-safe.
- If AWG removal is partially failed, operation exits with explicit retry-needed error and leaves diagnosable DB state.
