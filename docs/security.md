# Security model (beta)

## Runtime privileges

- Bot service runs as `awg-bot`.
- Bot is **not** added to `docker` group.
- AWG operations are executed via root-owned helper:
  - path: `/usr/local/libexec/awg-bot-helper`
  - policy: `/etc/awg-bot-helper.json` (root-owned, installer-managed)
  - sudoers rule: `/etc/sudoers.d/awg-bot-helper`
  - allowed operations only: `check-awg`, `show`, `genkey`, `pubkey`, `genpsk`, `add-peer`, `remove-peer`
- Helper does not accept arbitrary container from bot process anymore; container/interface are loaded from root-owned policy.
- Helper validates policy values, public keys and IPv4 values and does not use shell strings.

## Access revoke / delete consistency

- `revoke_user_access()` marks keys as `revoke_pending`, attempts AWG peer removal, and only after successful reconciliation sets `sub_until='0'`.
- If peer removal partially fails, revoke exits with retryable error and leaves `revoke_pending` state for safe retry.
- `delete_user_everywhere()` keeps analogous `delete_pending` model.

## Encryption

- New values are written as `enc:v2` (PBKDF2HMAC-SHA256).
- Legacy `enc:v1` values are still readable.
- Optional env: `ENCRYPTION_PBKDF2_ITERATIONS` (default `390000`).

## Remaining risks

- Helper still requires controlled `sudo` access for service user to perform privileged docker operations.
- Full host compromise risk is reduced compared to `docker` group and arbitrary `docker exec`, but helper path remains privileged and must be monitored.
