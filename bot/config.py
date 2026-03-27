import ipaddress
import logging
import os
import subprocess
from pathlib import Path

from dotenv import load_dotenv

ENV_FILE = Path('.env')
load_dotenv(ENV_FILE)

DEFAULT_ENV: dict[str, str] = {
    'SERVER_NAME': 'My VPN',
    'DOWNLOAD_URL': 'https://m-1-14-3w5hsuiikq-ez.a.run.app/ru/downloads',
    'PUBLIC_HOST': '',
    'SUPPORT_USERNAME': '',
    'AWG_I1': '<r 2><b 0x858000010001000000000669636c6f756403636f6d0000010001c00c000100010000105a00044d583737>',
    'AWG_PROTOCOL_VERSION': '2',
    'AWG_TRANSPORT_PROTO': 'udp',
    'DOCKER_CONTAINER': 'amnezia-awg2',
    'WG_INTERFACE': 'awg0',
    'DB_PATH': 'vpn_bot.db',
    'VPN_SUBNET_PREFIX': '10.8.1.',
    'FIRST_CLIENT_OCTET': '3',
    'MAX_CLIENT_OCTET': '254',
    'CONFIGS_PER_USER': '2',
    'CLEANUP_INTERVAL_SECONDS': '300',
    'PRIMARY_DNS': '1.1.1.1',
    'SECONDARY_DNS': '1.0.0.1',
    'CLIENT_MTU': '1376',
    'PERSISTENT_KEEPALIVE': '25',
    'CLIENT_ALLOWED_IPS': '0.0.0.0/0, ::/0',
    'STARS_PRICE_7_DAYS': '15',
    'STARS_PRICE_30_DAYS': '50',
    'PURCHASE_CLICK_COOLDOWN_SECONDS': '2',
    'PURCHASE_RATE_LIMIT_TTL_SECONDS': '3600',
    'ADMIN_COMMAND_COOLDOWN_SECONDS': '2',
    'DOCKER_RETRIES': '3',
    'DOCKER_RETRY_BASE_DELAY': '0.5',
    'DOCKER_TIMEOUT_SECONDS': '20',
    'AWG_HELPER_PATH': '/usr/local/libexec/awg-bot-helper',
    'AWG_HELPER_USE_SUDO': '1',
    'AWG_PEERS_CACHE_TTL_SECONDS': '5.0',
    'PENDING_KEY_TTL_SECONDS': '900',
    'PAYMENT_RETRY_DELAY_SECONDS': '60',
    'AWG_JC': '6',
    'AWG_JMIN': '10',
    'AWG_JMAX': '50',
    'AWG_S1': '37',
    'AWG_S2': '98',
    'AWG_S3': '47',
    'AWG_S4': '14',
    'AWG_H1': '1486401722-1692300209',
    'AWG_H2': '1696990121-1817276760',
    'AWG_H3': '1841833217-1995591429',
    'AWG_H4': '2109962185-2145796739',
    'AWG_I2': '',
    'AWG_I3': '',
    'AWG_I4': '',
    'AWG_I5': '',
    'IGNORE_PEERS': '',
}

logging.basicConfig(level=logging.INFO, format='%(asctime)s | %(levelname)s | %(message)s')
logger = logging.getLogger(__name__)


def _read_env_file(path: Path) -> dict[str, str]:
    data: dict[str, str] = {}
    if not path.exists():
        return data
    for raw_line in path.read_text(encoding='utf-8').splitlines():
        line = raw_line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, value = line.split('=', 1)
        data[key.strip()] = value.strip()
    return data


_existing_env = _read_env_file(ENV_FILE)


def _save_env_value(name: str, value: str) -> None:
    _existing_env[name] = value
    content = '\n'.join(f'{key}={val}' for key, val in sorted(_existing_env.items())) + '\n'
    ENV_FILE.write_text(content, encoding='utf-8')
    os.environ[name] = value


def save_env_value(name: str, value: str) -> None:
    _save_env_value(name, value)
    globals()[name] = value


def get_support_username() -> str:
    return globals().get('SUPPORT_USERNAME', '').strip() or '@support'


def get_download_url() -> str:
    return globals().get('DOWNLOAD_URL', '').strip() or DEFAULT_ENV['DOWNLOAD_URL']


def maybe_set_support_username(username: str | None) -> str:
    if not username:
        return get_support_username()
    normalized = username if username.startswith('@') else f'@{username}'
    globals()['SUPPORT_USERNAME'] = normalized
    return normalized


def _command_exists(name: str) -> bool:
    return subprocess.run(['bash', '-lc', f'command -v {name} >/dev/null 2>&1'], check=False).returncode == 0


def _run_local_command(args: list[str], timeout: int = 10) -> str:
    result = subprocess.run(args, check=False, capture_output=True, text=True, timeout=timeout)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or 'command failed')
    return result.stdout.strip()


def _docker_available() -> bool:
    if not _command_exists('docker'):
        return False
    try:
        _run_local_command(['docker', 'ps'], timeout=8)
        return True
    except Exception:
        return False


def _docker_exec(container: str, command: list[str], timeout: int = 10) -> str:
    return _run_local_command(['docker', 'exec', '-i', container, *command], timeout=timeout)


def _valid_container(name: str) -> bool:
    try:
        _run_local_command(['docker', 'inspect', name], timeout=8)
        return True
    except Exception:
        return False


def _find_awg_container() -> str:
    configured = os.getenv('DOCKER_CONTAINER', '').strip()
    if configured and _docker_available() and _valid_container(configured):
        return configured
    if not _docker_available():
        return configured or DEFAULT_ENV['DOCKER_CONTAINER']
    try:
        lines = _run_local_command(['docker', 'ps', '--format', '{{.Names}}\t{{.Image}}'], timeout=8).splitlines()
    except Exception:
        return configured or DEFAULT_ENV['DOCKER_CONTAINER']

    ranked: list[tuple[int, str]] = []
    patterns = [('amnezia-awg', 100), ('awg', 70), ('wireguard', 60), ('vpn', 30)]
    for raw in lines:
        parts = raw.split('\t', 1)
        name = parts[0].strip()
        image = parts[1].strip() if len(parts) > 1 else ''
        haystack = f'{name} {image}'.lower()
        score = 0
        for pattern, weight in patterns:
            if pattern in haystack:
                score += weight
        if score:
            ranked.append((score, name))
    if ranked:
        ranked.sort(reverse=True)
        return ranked[0][1]
    return configured or DEFAULT_ENV['DOCKER_CONTAINER']


def _is_public_ip(value: str) -> bool:
    try:
        addr = ipaddress.ip_address(value)
        return addr.version == 4 and not (addr.is_private or addr.is_loopback or addr.is_link_local or addr.is_multicast or addr.is_unspecified)
    except ValueError:
        return False


def _resolve_public_ipv4(value: str) -> str:
    value = value.strip()
    if not value:
        return ''
    if _is_public_ip(value):
        return value
    return ''


def _detect_public_host() -> str:
    direct = _resolve_public_ipv4(os.getenv('PUBLIC_HOST', '').strip())
    if direct:
        return direct

    if _command_exists('curl'):
        for url in ('https://api.ipify.org', 'https://ifconfig.me/ip', 'https://ipv4.icanhazip.com'):
            try:
                value = _run_local_command(['curl', '-4', '-fsSL', url], timeout=8).strip()
                if _is_public_ip(value):
                    return value
            except Exception:
                continue
    return ''


def _parse_subnet_prefix(show_output: str) -> str:
    prefixes: list[str] = []
    for line in show_output.splitlines():
        lowered = line.strip().lower()
        if not lowered.startswith('allowed ips:'):
            continue
        ips_part = line.split(':', 1)[1]
        for piece in ips_part.split(','):
            token = piece.strip().split('/')[0]
            octets = token.split('.')
            if len(octets) == 4 and all(part.isdigit() for part in octets):
                prefixes.append('.'.join(octets[:3]) + '.')
    if not prefixes:
        return ''
    return max(set(prefixes), key=prefixes.count)


def _detect_awg_from_container(container: str, interface_hint: str) -> dict[str, str]:
    detected: dict[str, str] = {}
    if not container or not _docker_available() or not _valid_container(container):
        return detected

    show_output = ''
    last_error: Exception | None = None
    for cmd in ([['awg', 'show', interface_hint], ['awg', 'show']]):
        try:
            show_output = _docker_exec(container, cmd, timeout=12)
            if show_output:
                break
        except Exception as e:
            last_error = e
    if not show_output:
        if last_error:
            logger.info('Автоопределение AWG пропущено: %s', last_error)
        return detected

    mapping = {
        'jc:': 'AWG_JC',
        'jmin:': 'AWG_JMIN',
        'jmax:': 'AWG_JMAX',
        's1:': 'AWG_S1',
        's2:': 'AWG_S2',
        's3:': 'AWG_S3',
        's4:': 'AWG_S4',
        'h1:': 'AWG_H1',
        'h2:': 'AWG_H2',
        'h3:': 'AWG_H3',
        'h4:': 'AWG_H4',
    }

    for raw_line in show_output.splitlines():
        line = raw_line.strip()
        lowered = line.lower()
        if lowered.startswith('interface: '):
            detected['WG_INTERFACE'] = line.split(':', 1)[1].strip()
            continue
        if lowered.startswith('public key: '):
            detected['SERVER_PUBLIC_KEY'] = line.split(':', 1)[1].strip()
            continue
        if lowered.startswith('listening port: '):
            detected['DETECTED_AWG_PORT'] = line.split(':', 1)[1].strip()
            continue
        for prefix, env_name in mapping.items():
            if lowered.startswith(prefix):
                detected[env_name] = line.split(':', 1)[1].strip()
                break

    subnet_prefix = _parse_subnet_prefix(show_output)
    if subnet_prefix:
        detected['VPN_SUBNET_PREFIX'] = subnet_prefix

    port_value = detected.get('DETECTED_AWG_PORT', '').strip()
    if port_value:
        try:
            host_port_output = _run_local_command(['docker', 'port', container, f'{port_value}/udp'], timeout=10)
            first_line = host_port_output.strip().splitlines()[0]
            host_port = first_line.rsplit(':', 1)[-1].strip()
            if host_port:
                detected['DETECTED_HOST_PORT'] = host_port
        except Exception:
            detected['DETECTED_HOST_PORT'] = port_value

    return detected


def _env_with_runtime_default(name: str, default: str) -> str:
    value = os.getenv(name, '').strip()
    if value:
        return value
    return default


AUTO_DETECT_ON_IMPORT = os.getenv('CONFIG_AUTODETECT_ON_IMPORT', '0').strip() == '1'
if AUTO_DETECT_ON_IMPORT:
    DOCKER_CONTAINER_HINT = _find_awg_container()
    WG_INTERFACE_HINT = _env_with_runtime_default('WG_INTERFACE', DEFAULT_ENV['WG_INTERFACE'])
    _detected_awg = _detect_awg_from_container(DOCKER_CONTAINER_HINT, WG_INTERFACE_HINT)
    PUBLIC_HOST_HINT = _env_with_runtime_default('PUBLIC_HOST', _detect_public_host())
else:
    DOCKER_CONTAINER_HINT = _env_with_runtime_default('DOCKER_CONTAINER', DEFAULT_ENV['DOCKER_CONTAINER'])
    WG_INTERFACE_HINT = _env_with_runtime_default('WG_INTERFACE', DEFAULT_ENV['WG_INTERFACE'])
    _detected_awg = {}
    PUBLIC_HOST_HINT = _env_with_runtime_default('PUBLIC_HOST', DEFAULT_ENV['PUBLIC_HOST'])
_raw_public_host = os.getenv('PUBLIC_HOST', '').strip()
PUBLIC_HOST_HINT = _resolve_public_ipv4(PUBLIC_HOST_HINT)
_public_host_error = ''
if _raw_public_host and not PUBLIC_HOST_HINT:
    _public_host_error = 'PUBLIC_HOST (ожидается публичный IPv4 без порта)'
    logger.warning('PUBLIC_HOST задан некорректно: %r', _raw_public_host)
SERVER_NAME_HINT = os.getenv('SERVER_NAME', DEFAULT_ENV['SERVER_NAME']).strip() or DEFAULT_ENV['SERVER_NAME']
SERVER_PUBLIC_KEY_HINT = _env_with_runtime_default('SERVER_PUBLIC_KEY', _detected_awg.get('SERVER_PUBLIC_KEY', '').strip())
DETECTED_HOST_PORT_HINT = _detected_awg.get('DETECTED_HOST_PORT', '').strip()

_raw_server_ip = os.getenv('SERVER_IP', '').strip()
SERVER_IP_HINT = ''
_server_ip_error = ''
if _raw_server_ip:
    if ':' in _raw_server_ip:
        raw_host, raw_port = _raw_server_ip.rsplit(':', 1)
        resolved_host = _resolve_public_ipv4(raw_host)
        if resolved_host and raw_port.isdigit() and 1 <= int(raw_port) <= 65535:
            SERVER_IP_HINT = f'{resolved_host}:{raw_port}'
        else:
            _server_ip_error = 'SERVER_IP (ожидается публичный IPv4:port)'
            logger.warning('SERVER_IP задан некорректно: %r', _raw_server_ip)
    else:
        _server_ip_error = 'SERVER_IP (ожидается публичный IPv4:port)'
        logger.warning('SERVER_IP задан некорректно: %r', _raw_server_ip)

if not SERVER_IP_HINT and PUBLIC_HOST_HINT and DETECTED_HOST_PORT_HINT:
    SERVER_IP_HINT = f'{PUBLIC_HOST_HINT}:{DETECTED_HOST_PORT_HINT}'

if AUTO_DETECT_ON_IMPORT and _detected_awg:
    summary_parts = []
    if _detected_awg.get('WG_INTERFACE'):
        summary_parts.append(f'container={DOCKER_CONTAINER_HINT}')
        summary_parts.append(f'interface={_detected_awg["WG_INTERFACE"]}')
    if _detected_awg.get('SERVER_PUBLIC_KEY'):
        summary_parts.append('public_key=найден')
    if _detected_awg.get('DETECTED_HOST_PORT'):
        summary_parts.append(f'port={_detected_awg["DETECTED_HOST_PORT"]}')
    if SERVER_IP_HINT:
        summary_parts.append(f'endpoint={SERVER_IP_HINT}')
    logger.info('Автоопределение AWG: %s', ', '.join(summary_parts))


def env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None or value == '':
        return default
    try:
        return int(value)
    except ValueError as e:
        raise RuntimeError(f'Некорректное целое число в {name}: {value}') from e


def env_float(name: str, default: float) -> float:
    value = os.getenv(name)
    if value is None or value == '':
        return default
    try:
        return float(value)
    except ValueError as e:
        raise RuntimeError(f'Некорректное число в {name}: {value}') from e


API_TOKEN = os.getenv('API_TOKEN', '').strip()
ADMIN_ID = env_int('ADMIN_ID', 0)

SERVER_PUBLIC_KEY = SERVER_PUBLIC_KEY_HINT
SERVER_IP = SERVER_IP_HINT
PUBLIC_HOST = PUBLIC_HOST_HINT

DOCKER_CONTAINER = _env_with_runtime_default('DOCKER_CONTAINER', DOCKER_CONTAINER_HINT or DEFAULT_ENV['DOCKER_CONTAINER'])
WG_INTERFACE = _env_with_runtime_default('WG_INTERFACE', _detected_awg.get('WG_INTERFACE', '').strip() or DEFAULT_ENV['WG_INTERFACE'])
DB_PATH = _env_with_runtime_default('DB_PATH', DEFAULT_ENV['DB_PATH'])

DOWNLOAD_URL = _env_with_runtime_default('DOWNLOAD_URL', DEFAULT_ENV['DOWNLOAD_URL'])
SUPPORT_USERNAME = _env_with_runtime_default('SUPPORT_USERNAME', DEFAULT_ENV['SUPPORT_USERNAME'])
SERVER_NAME = SERVER_NAME_HINT

STARS_PRICE_7_DAYS = env_int('STARS_PRICE_7_DAYS', int(DEFAULT_ENV['STARS_PRICE_7_DAYS']))
STARS_PRICE_30_DAYS = env_int('STARS_PRICE_30_DAYS', int(DEFAULT_ENV['STARS_PRICE_30_DAYS']))

VPN_SUBNET_PREFIX = _env_with_runtime_default('VPN_SUBNET_PREFIX', _detected_awg.get('VPN_SUBNET_PREFIX', '').strip() or DEFAULT_ENV['VPN_SUBNET_PREFIX'])
FIRST_CLIENT_OCTET = env_int('FIRST_CLIENT_OCTET', int(DEFAULT_ENV['FIRST_CLIENT_OCTET']))
MAX_CLIENT_OCTET = env_int('MAX_CLIENT_OCTET', int(DEFAULT_ENV['MAX_CLIENT_OCTET']))
CONFIGS_PER_USER = env_int('CONFIGS_PER_USER', int(DEFAULT_ENV['CONFIGS_PER_USER']))
CLEANUP_INTERVAL_SECONDS = env_int('CLEANUP_INTERVAL_SECONDS', int(DEFAULT_ENV['CLEANUP_INTERVAL_SECONDS']))

PRIMARY_DNS = _env_with_runtime_default('PRIMARY_DNS', DEFAULT_ENV['PRIMARY_DNS'])
SECONDARY_DNS = _env_with_runtime_default('SECONDARY_DNS', DEFAULT_ENV['SECONDARY_DNS'])
CLIENT_MTU = _env_with_runtime_default('CLIENT_MTU', DEFAULT_ENV['CLIENT_MTU'])
PERSISTENT_KEEPALIVE = _env_with_runtime_default('PERSISTENT_KEEPALIVE', DEFAULT_ENV['PERSISTENT_KEEPALIVE'])
CLIENT_ALLOWED_IPS = _env_with_runtime_default('CLIENT_ALLOWED_IPS', DEFAULT_ENV['CLIENT_ALLOWED_IPS'])
ENCRYPTION_SECRET = os.getenv('ENCRYPTION_SECRET', '').strip()
IGNORE_PEERS = [p.strip() for p in os.getenv('IGNORE_PEERS', DEFAULT_ENV['IGNORE_PEERS']).split(',') if p.strip()]

AWG_JC = _env_with_runtime_default('AWG_JC', _detected_awg.get('AWG_JC', '').strip() or DEFAULT_ENV['AWG_JC'])
AWG_JMIN = _env_with_runtime_default('AWG_JMIN', _detected_awg.get('AWG_JMIN', '').strip() or DEFAULT_ENV['AWG_JMIN'])
AWG_JMAX = _env_with_runtime_default('AWG_JMAX', _detected_awg.get('AWG_JMAX', '').strip() or DEFAULT_ENV['AWG_JMAX'])
AWG_S1 = _env_with_runtime_default('AWG_S1', _detected_awg.get('AWG_S1', '').strip() or DEFAULT_ENV['AWG_S1'])
AWG_S2 = _env_with_runtime_default('AWG_S2', _detected_awg.get('AWG_S2', '').strip() or DEFAULT_ENV['AWG_S2'])
AWG_S3 = _env_with_runtime_default('AWG_S3', _detected_awg.get('AWG_S3', '').strip() or DEFAULT_ENV['AWG_S3'])
AWG_S4 = _env_with_runtime_default('AWG_S4', _detected_awg.get('AWG_S4', '').strip() or DEFAULT_ENV['AWG_S4'])
AWG_H1 = _env_with_runtime_default('AWG_H1', _detected_awg.get('AWG_H1', '').strip() or DEFAULT_ENV['AWG_H1'])
AWG_H2 = _env_with_runtime_default('AWG_H2', _detected_awg.get('AWG_H2', '').strip() or DEFAULT_ENV['AWG_H2'])
AWG_H3 = _env_with_runtime_default('AWG_H3', _detected_awg.get('AWG_H3', '').strip() or DEFAULT_ENV['AWG_H3'])
AWG_H4 = _env_with_runtime_default('AWG_H4', _detected_awg.get('AWG_H4', '').strip() or DEFAULT_ENV['AWG_H4'])
AWG_I1 = _env_with_runtime_default('AWG_I1', DEFAULT_ENV['AWG_I1'])
AWG_I2 = _env_with_runtime_default('AWG_I2', DEFAULT_ENV['AWG_I2'])
AWG_I3 = _env_with_runtime_default('AWG_I3', DEFAULT_ENV['AWG_I3'])
AWG_I4 = _env_with_runtime_default('AWG_I4', DEFAULT_ENV['AWG_I4'])
AWG_I5 = _env_with_runtime_default('AWG_I5', DEFAULT_ENV['AWG_I5'])
AWG_PROTOCOL_VERSION = _env_with_runtime_default('AWG_PROTOCOL_VERSION', DEFAULT_ENV['AWG_PROTOCOL_VERSION'])
AWG_TRANSPORT_PROTO = _env_with_runtime_default('AWG_TRANSPORT_PROTO', DEFAULT_ENV['AWG_TRANSPORT_PROTO'])

PURCHASE_CLICK_COOLDOWN_SECONDS = env_int('PURCHASE_CLICK_COOLDOWN_SECONDS', int(DEFAULT_ENV['PURCHASE_CLICK_COOLDOWN_SECONDS']))
PURCHASE_RATE_LIMIT_TTL_SECONDS = env_int('PURCHASE_RATE_LIMIT_TTL_SECONDS', int(DEFAULT_ENV['PURCHASE_RATE_LIMIT_TTL_SECONDS']))
ADMIN_COMMAND_COOLDOWN_SECONDS = env_int('ADMIN_COMMAND_COOLDOWN_SECONDS', int(DEFAULT_ENV['ADMIN_COMMAND_COOLDOWN_SECONDS']))
DOCKER_RETRIES = env_int('DOCKER_RETRIES', int(DEFAULT_ENV['DOCKER_RETRIES']))
DOCKER_RETRY_BASE_DELAY = env_float('DOCKER_RETRY_BASE_DELAY', float(DEFAULT_ENV['DOCKER_RETRY_BASE_DELAY']))
DOCKER_TIMEOUT_SECONDS = env_int('DOCKER_TIMEOUT_SECONDS', int(DEFAULT_ENV['DOCKER_TIMEOUT_SECONDS']))
AWG_HELPER_PATH = _env_with_runtime_default('AWG_HELPER_PATH', DEFAULT_ENV['AWG_HELPER_PATH'])
AWG_HELPER_USE_SUDO = env_int('AWG_HELPER_USE_SUDO', int(DEFAULT_ENV['AWG_HELPER_USE_SUDO'])) == 1
AWG_PEERS_CACHE_TTL_SECONDS = env_float('AWG_PEERS_CACHE_TTL_SECONDS', float(DEFAULT_ENV['AWG_PEERS_CACHE_TTL_SECONDS']))
PENDING_KEY_TTL_SECONDS = env_int('PENDING_KEY_TTL_SECONDS', int(DEFAULT_ENV['PENDING_KEY_TTL_SECONDS']))
PAYMENT_RETRY_DELAY_SECONDS = env_int('PAYMENT_RETRY_DELAY_SECONDS', int(DEFAULT_ENV['PAYMENT_RETRY_DELAY_SECONDS']))

required_missing = []
if not API_TOKEN:
    required_missing.append('API_TOKEN')
if ADMIN_ID <= 0:
    required_missing.append('ADMIN_ID')
if not SERVER_PUBLIC_KEY:
    required_missing.append('SERVER_PUBLIC_KEY')
if not SERVER_IP:
    required_missing.append(_server_ip_error or 'SERVER_IP')
if _public_host_error:
    required_missing.append(_public_host_error)
if not ENCRYPTION_SECRET:
    required_missing.append('ENCRYPTION_SECRET')
if required_missing:
    raise RuntimeError(
        'Не заданы или некорректны переменные окружения: '
        + ', '.join(required_missing)
        + '. Запусти установщик awg-tgbot.sh или заполни .env вручную.'
    )
