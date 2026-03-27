# Migration notes

## 1) Privilege model migration

After update, installer will:

1. install helper `/usr/local/libexec/awg-bot-helper` (root-owned),
2. create sudoers file `/etc/sudoers.d/awg-bot-helper`,
3. stop using `docker` group membership for runtime bot user.

If bot was previously in `docker` group, this is no longer required.

## 2) Encryption migration

- Existing encrypted records (`enc:v1`) continue to decrypt normally.
- New/updated records are stored as `enc:v2` automatically.
- No DB schema migration required for encryption change.

## 3) Delete flow migration

- `delete_user_everywhere()` now treats `delete_pending` as retryable state.
- User row is not deleted while peer deletion is still pending.

## 4) Manual checks after update

1. `sudo awg-tgbot status`
2. `systemctl status vpn-bot.service --no-pager -l`
3. `journalctl -u vpn-bot.service -n 100 --no-pager`
4. In app logs verify helper operations are successful and no `sudo` permission errors.
