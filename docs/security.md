# Security model (beta)

## Runtime privileges

- Bot service runs as `awg-bot`.
- Bot is **not** added to `docker` group.
- AWG operations are executed via root-owned helper:
  - path: `/usr/local/libexec/awg-bot-helper`
  - policy: `/etc/awg-bot-helper.json`
  - sudoers rule: `/etc/sudoers.d/awg-bot-helper`
  - allowed operations only: `check-awg`, `show`, `genkey`, `pubkey`, `genpsk`, `add-peer`, `remove-peer`
- Helper validates container/interface names, public keys and IPv4 values and does not use shell strings.
- Helper denies requests outside policy target (`container/interface`) and performs policy-file hardening checks (exists, regular file, not symlink, root-owned, not group/world writable).

## Installer safety (TTY / destructive actions)

- Prompt-based flows safe-fail without TTY.
- Interactive menu is blocked without TTY.
- `remove-default` now requires explicit confirmation (`y/n`) and is not auto-confirmed by implicit defaults.
- Safe non-interactive commands remain available (`status`, `check-updates`, `update`, `sync-helper-policy`) when no input is needed.

## Source of truth and synchronization

- Runtime reads `DOCKER_CONTAINER`/`WG_INTERFACE` from `.env`.
- Helper independently enforces policy from `/etc/awg-bot-helper.json`.
- Installer syncs policy from `.env` during install/update and validates values before writing policy.
- `awg-tgbot status` displays both env target and policy target; on mismatch operator must run `sudo awg-tgbot sync-helper-policy`.

## Destructive admin actions safety

- Orphan cleanup is explicitly two-step:
  - `/clean_orphans` and admin inline cleanup only move orphan peers into quarantine protection;
  - `/clean_orphans_force` performs physical peer deletion.
- `revoke`/`delete` flows keep DB states (`revoke_pending` / `delete_pending`) on partial AWG failures to avoid silent data loss.

## Encryption

- New values are written as `enc:v2` (PBKDF2HMAC-SHA256).
- Legacy `enc:v1` values are still readable.
- Optional env: `ENCRYPTION_PBKDF2_ITERATIONS` (default `390000`).

## Remaining risks

- Helper still requires controlled `sudo` access for service user to perform privileged docker operations.
- Full host compromise is reduced compared to `docker` group, but this remains privileged code-path and must be monitored.
- Manual edits of `.env` without policy sync can still break runtime operations; explicit status warning and `sync-helper-policy` reduce this risk but do not remove operator error entirely.
