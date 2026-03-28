# Cleanup audit — 2026-03-28

## Scope
- `bot/*.py`
- `tests/*.py`
- `awg-tgbot.sh`
- `README.md`, `docs/*.md`

## High-confidence findings

1. `bot/awg_backend.py`: variable `allowed_force` is calculated but never used in `clean_orphan_awg_peers(force=True)`.
2. `bot/handlers_admin.py`: unused import `asyncio`.
3. `bot/helpers.py`: helper `price_suffix` is not used anywhere in repo.
4. `tests/test_beta_blockers.py`: local import `database` is redundant in `test_issue_subscription_operation_id_is_idempotent`.
5. `bot/handlers_user.py`: duplicated logic for buy menu text/markup in `_send_buy_menu` and `buy` handler.
6. `awg-tgbot.sh`: functions `configure_auto_install` and `configure_manual_install` appear orphaned (declared but not called).
7. `awg-tgbot.sh`: helper `print_file_matches_tty_safe` appears orphaned (declared but not called).

## Structural observations
- Monolithic installer/runtime script `awg-tgbot.sh` mixes install/update/remove/log/diagnostic responsibilities in one file (2k+ LOC), which increases maintenance cost.
- Bot runtime code is reasonably modular (`handlers_*`, `payments`, `database`, `awg_backend`), but some helper/UI logic is duplicated between callback and message flows.

## Artifact/files scan
- No committed cache/build artifact directories found (`__pycache__`, `.pytest_cache`, `.mypy_cache`, `build`, `dist`, `tmp`, `old`, `legacy`).
- No backup files (`*.bak`, `*.old`, `*~`) found in tracked tree.

## Suggested cleanup order
1. Remove guaranteed-dead code/imports (`allowed_force`, `asyncio`, `price_suffix`, redundant test import).
2. Deduplicate buy menu generation by reusing `_send_buy_menu` from `buy` handler.
3. Validate whether orphan shell functions are intentionally reserved for future flows; remove or document.
4. Consider splitting `awg-tgbot.sh` into logical modules over time.
