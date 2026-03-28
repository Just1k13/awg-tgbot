# Migration notes

## 1) Privilege model migration

After update, installer will:

1. install helper `/usr/local/libexec/awg-bot-helper` (root-owned),
2. create sudoers file `/etc/sudoers.d/awg-bot-helper`,
3. create/update helper policy `/etc/awg-bot-helper.json` (root-owned, non-writable by group/others),
4. stop using `docker` group membership for runtime bot user.

If bot was previously in `docker` group, this is no longer required.

Also note:
- `sync-helper-policy` writes policy from current `.env` (`DOCKER_CONTAINER` + `WG_INTERFACE`);
- runtime startup checks hard-fail on `.env` vs helper policy mismatch, so this sync step is mandatory after manual target edits.

## 2) Encryption migration

- Existing encrypted records (`enc:v1`) continue to decrypt normally.
- New/updated records are stored as `enc:v2` automatically.
- No DB schema migration required for encryption change.

## 3) Delete flow migration

- `delete_user_everywhere()` now treats `delete_pending` as retryable state.
- User row is not deleted while peer deletion is still pending.

## 4) Non-interactive safety changes

- Installer now safe-fails when prompt input is required but no TTY is available.
- Interactive menu without TTY exits with explicit error.
- Destructive action `remove-default` requires explicit `y/n` input (no implicit default confirm).

## 5) Manual checks after update

1. `sudo awg-tgbot status`
2. `systemctl status vpn-bot.service --no-pager -l`
3. `journalctl -u vpn-bot.service -n 100 --no-pager`
4. Verify `.env` vs helper policy target in `status` output.
5. If `DOCKER_CONTAINER`/`WG_INTERFACE` were changed manually, run:
   - `sudo awg-tgbot sync-helper-policy`
   - `sudo awg-tgbot status`
6. In app logs verify helper operations are successful and no `sudo` permission errors.
7. Validate orphan flow semantics in admin UX:
   - `/clean_orphans` performs quarantine only;
   - `/clean_orphans_force` performs physical deletion.
8. If you used a custom DB path in old installation, verify `DB_PATH` in `.env` remained unchanged after update.
