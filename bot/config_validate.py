import json
from pathlib import Path


def read_helper_policy(path: Path) -> tuple[str, str, str]:
    if not path.exists():
        return '', '', f'helper policy not found: {path}'
    if path.is_symlink():
        return '', '', f'helper policy must not be symlink: {path}'
    try:
        raw = json.loads(path.read_text(encoding='utf-8'))
    except PermissionError as e:
        return '', '', f'helper policy unreadable by runtime user: {e}'
    except Exception as e:
        return '', '', f'helper policy parse failed: {e}'
    if not isinstance(raw, dict):
        return '', '', 'helper policy must be a JSON object'
    container = str(raw.get('container', '')).strip()
    interface = str(raw.get('interface', '')).strip()
    return container, interface, ''


def validate_required_env(
    *,
    api_token: str,
    admin_id: int,
    server_public_key: str,
    server_ip: str,
    encryption_secret: str,
    server_ip_error: str,
    public_host_error: str,
) -> None:
    required_missing = []
    if not api_token:
        required_missing.append('API_TOKEN')
    if admin_id <= 0:
        required_missing.append('ADMIN_ID')
    if not server_public_key:
        required_missing.append('SERVER_PUBLIC_KEY')
    if not server_ip:
        required_missing.append(server_ip_error or 'SERVER_IP')
    if public_host_error:
        required_missing.append(public_host_error)
    if not encryption_secret:
        required_missing.append('ENCRYPTION_SECRET')
    if required_missing:
        raise RuntimeError(
            'Не заданы или некорректны переменные окружения: '
            + ', '.join(required_missing)
            + '. Запусти установщик awg-tgbot.sh или заполни .env вручную.'
        )


def validate_helper_policy(*, policy_path: str, docker_container: str, wg_interface: str, logger) -> None:
    policy_container, policy_interface, policy_error = read_helper_policy(Path(policy_path))
    if policy_error:
        if policy_error.startswith('helper policy unreadable by runtime user:'):
            logger.info('AWG helper policy status: %s', policy_error)
        else:
            logger.warning('AWG helper policy status: %s', policy_error)
    elif policy_container != docker_container or policy_interface != wg_interface:
        raise RuntimeError(
            'AWG helper policy mismatch: '
            f'env={docker_container}/{wg_interface} policy={policy_container}/{policy_interface}. '
            'Выполни sync-helper-policy в installer.'
        )
