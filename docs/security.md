# Security model (beta)

## Runtime privileges

- Bot service runs as `awg-bot`.
- Bot is **not** added to `docker` group.
- AWG operations are executed via root-owned helper:
  - path: `/usr/local/libexec/awg-bot-helper`
  - sudoers rule: `/etc/sudoers.d/awg-bot-helper`
  - allowed operations only: `check-awg`, `show`, `genkey`, `pubkey`, `genpsk`, `add-peer`, `remove-peer`
- Helper validates container/interface names, public keys and IPv4 values and does not use shell strings.

## Encryption

- New values are written as `enc:v2` (PBKDF2HMAC-SHA256).
- Legacy `enc:v1` values are still readable.
- Optional env: `ENCRYPTION_PBKDF2_ITERATIONS` (default `390000`).

## Remaining risks

- Helper still requires controlled `sudo` access for service user to perform privileged docker operations.
- Full host compromise is reduced compared to `docker` group, but this remains privileged code-path and must be monitored.
