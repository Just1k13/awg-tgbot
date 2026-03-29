import json
from ipaddress import ip_network
from pathlib import Path
import re


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
            logger.error('AWG helper policy status: %s', policy_error)
            raise RuntimeError(
                'исправьте права на /etc/awg-bot-helper.json или выполните sudo awg-tgbot sync-helper-policy'
            )
        else:
            logger.warning('AWG helper policy status: %s', policy_error)
    elif policy_container != docker_container or policy_interface != wg_interface:
        raise RuntimeError(
            'AWG helper policy mismatch: '
            f'env={docker_container}/{wg_interface} policy={policy_container}/{policy_interface}. '
            'Выполни sync-helper-policy в installer.'
        )


def _parse_non_negative_int(value: str, field: str) -> int:
    raw = str(value).strip()
    if not raw:
        return 0
    if not raw.isdigit():
        raise RuntimeError(f'{field} должен быть целым числом >= 0')
    return int(raw)


def validate_awg_obfuscation_settings(
    *,
    awg_jc: str,
    awg_jmin: str,
    awg_jmax: str,
    awg_s1: str,
    awg_s2: str,
    awg_s3: str,
    awg_s4: str,
    awg_h1: str,
    awg_h2: str,
    awg_h3: str,
    awg_h4: str,
    awg_i1: str,
    awg_i2: str,
    awg_i3: str,
    awg_i4: str,
    awg_i5: str,
) -> None:
    """
    Fail fast only for known-invalid numeric settings.
    Keep I1..I5 semantics aligned with upstream amneziawg-go behavior.
    """
    jmin = _parse_non_negative_int(awg_jmin, 'AWG_JMIN')
    jmax = _parse_non_negative_int(awg_jmax, 'AWG_JMAX')
    jc = _parse_non_negative_int(awg_jc, 'AWG_JC')
    if jc > 65535:
        raise RuntimeError('AWG_JC должен быть в диапазоне 0..65535')
    if jmin > 65535:
        raise RuntimeError('AWG_JMIN должен быть в диапазоне 0..65535')
    if jmax > 65535:
        raise RuntimeError('AWG_JMAX должен быть в диапазоне 0..65535')
    if jmin > jmax:
        raise RuntimeError('AWG_JMIN не может быть больше AWG_JMAX')

    for field, raw in (('AWG_S1', awg_s1), ('AWG_S2', awg_s2), ('AWG_S3', awg_s3), ('AWG_S4', awg_s4)):
        value = _parse_non_negative_int(raw, field)
        if value > 65535:
            raise RuntimeError(f'{field} должен быть в диапазоне 0..65535')

    for field, raw in (('AWG_H1', awg_h1), ('AWG_H2', awg_h2), ('AWG_H3', awg_h3), ('AWG_H4', awg_h4)):
        value = str(raw).strip()
        if value and not re.fullmatch(r'\d+-\d+', value):
            raise RuntimeError(f'{field} должен быть в формате N-N')


def validate_persistent_keepalive(raw_value: str) -> str:
    value = str(raw_value).strip()
    if value.lower() in {'off', '0'}:
        return '0'
    if not value.isdigit():
        raise RuntimeError('PERSISTENT_KEEPALIVE должен быть 0/off или целым числом 1..65535')
    parsed = int(value)
    if parsed < 1 or parsed > 65535:
        raise RuntimeError('PERSISTENT_KEEPALIVE должен быть 0/off или целым числом 1..65535')
    return str(parsed)


def validate_client_allowed_ips(raw_value: str) -> str:
    parts = [item.strip() for item in str(raw_value).split(',') if item.strip()]
    if not parts:
        raise RuntimeError('CLIENT_ALLOWED_IPS должен быть непустым списком CIDR через запятую')
    normalized: list[str] = []
    for cidr in parts:
        try:
            network = ip_network(cidr, strict=False)
        except ValueError as e:
            raise RuntimeError(f'CLIENT_ALLOWED_IPS содержит некорректный CIDR: {cidr}') from e
        normalized.append(str(network))
    return ', '.join(normalized)
