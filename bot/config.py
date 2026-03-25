import ipaddress
import logging
import os
import re
import secrets
import socket
import subprocess
import sys
from pathlib import Path

from dotenv import load_dotenv

ENV_FILE = Path('.env')
load_dotenv(ENV_FILE)


DEFAULT_ENV: dict[str, str] = {
    'DOWNLOAD_URL': 'https://amnezia.org',
    'PUBLIC_HOST': '',
    'SERVER_HOST': '',
    'SERVER_DOMAIN': '',
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
    'PRIMARY_DNS': '172.29.172.254',
    'SECONDARY_DNS': '1.0.0.1',
    'CLIENT_MTU': '1280',
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
    'AWG_PEERS_CACHE_TTL_SECONDS': '5.0',
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


logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)s | %(message)s',
)
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
    current = globals().get('SUPPORT_USERNAME', '').strip()
    if current == normalized:
        return normalized
    save_env_value('SUPPORT_USERNAME', normalized)
    return normalized


def _set_default(name: str, value: str) -> None:
    if not os.getenv(name, '').strip() and value is not None:
        os.environ[name] = value
        _existing_env.setdefault(name, value)


def _command_exists(name: str) -> bool:
    return subprocess.run(['bash', '-lc', f'command -v {name} >/dev/null 2>&1'], check=False).returncode == 0


def _run_local_command(args: list[str], timeout: int = 10) -> str:
    result = subprocess.run(
        args,
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
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
    patterns = [
        ('amnezia-awg', 100),
        ('awg', 70),
        ('wireguard', 60),
        ('vpn', 30),
    ]
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


def _is_ip(value: str) -> bool:
    try:
        ipaddress.ip_address(value)
        return True
    except ValueError:
        return False


def _is_public_ip(value: str) -> bool:
    try:
        addr = ipaddress.ip_address(value)
        return not (addr.is_private or addr.is_loopback or addr.is_link_local or addr.is_multicast or addr.is_unspecified)
    except ValueError:
        return False


def _hostname_like(value: str) -> bool:
    if not value or ' ' in value or ':' in value:
        return False
    if len(value) > 253:
        return False
    return bool(re.fullmatch(r'[A-Za-z0-9.-]+', value))


def _detect_public_host() -> str:
    for env_name in ('PUBLIC_HOST', 'SERVER_HOST', 'SERVER_DOMAIN'):
        direct = os.getenv(env_name, '').strip()
        if direct:
            return direct

    if _command_exists('curl'):
        endpoints = [
            'https://api.ipify.org',
            'https://ifconfig.me/ip',
            'https://ipv4.icanhazip.com',
        ]
        for url in endpoints:
            try:
                value = _run_local_command(['curl', '-4', '-fsSL', url], timeout=8).strip()
                if _is_public_ip(value):
                    return value
            except Exception:
                continue

    try:
        route = _run_local_command(['ip', '-4', 'route', 'get', '1.1.1.1'], timeout=6)
        match = re.search(r'\bsrc\s+(\d+\.\d+\.\d+\.\d+)\b', route)
        if match:
            return match.group(1)
    except Exception:
        pass

    try:
        values = _run_local_command(['hostname', '-I'], timeout=6).split()
        public_candidates = [v for v in values if _is_public_ip(v)]
        if public_candidates:
            return public_candidates[0]
    except Exception:
        pass

    try:
        host = socket.getfqdn().strip()
        if _hostname_like(host) and host.lower() != 'localhost':
            return host
    except Exception:
        pass

    return ''


def _detect_server_name() -> str:
    direct = os.getenv('SERVER_NAME', '').strip()
    if direct:
        return direct
    try:
        value = _run_local_command(['hostname'], timeout=5).strip()
        if value:
            return value
    except Exception:
        pass
    return 'My VPN'


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

    interface_name = ''
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
            interface_name = line.split(':', 1)[1].strip()
            detected['WG_INTERFACE'] = interface_name
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


for key, value in DEFAULT_ENV.items():
    _set_default(key, value)

DOCKER_CONTAINER_HINT = _find_awg_container()
_set_default('DOCKER_CONTAINER', DOCKER_CONTAINER_HINT)
WG_INTERFACE_HINT = os.getenv('WG_INTERFACE', DEFAULT_ENV['WG_INTERFACE']).strip() or DEFAULT_ENV['WG_INTERFACE']
_detected_awg = _detect_awg_from_container(DOCKER_CONTAINER_HINT, WG_INTERFACE_HINT)
for _k, _v in _detected_awg.items():
    if _k.startswith('DETECTED_'):
        os.environ[_k] = _v
        continue
    if _v and not os.getenv(_k, '').strip():
        os.environ[_k] = _v
        _existing_env.setdefault(_k, _v)

if not os.getenv('ENCRYPTION_SECRET', '').strip():
    generated_secret = secrets.token_urlsafe(32)
    os.environ['ENCRYPTION_SECRET'] = generated_secret
    _existing_env.setdefault('ENCRYPTION_SECRET', generated_secret)

if not os.getenv('SERVER_NAME', '').strip():
    os.environ['SERVER_NAME'] = _detect_server_name()
    _existing_env.setdefault('SERVER_NAME', os.environ['SERVER_NAME'])

if not os.getenv('SERVER_IP', '').strip():
    public_host = _detect_public_host()
    detected_port = os.getenv('DETECTED_HOST_PORT', '').strip() or _detected_awg.get('DETECTED_HOST_PORT', '').strip()
    if public_host and detected_port:
        os.environ['SERVER_IP'] = f'{public_host}:{detected_port}'
        _existing_env.setdefault('SERVER_IP', os.environ['SERVER_IP'])

REQUIRED_PROMPTS: dict[str, str] = {
    'API_TOKEN': 'Введите токен Telegram-бота (API_TOKEN, не numeric ID)',
    'ADMIN_ID': 'Введите Telegram user_id администратора (ADMIN_ID)',
}

if _detected_awg:
    summary_parts = []
    if _detected_awg.get('WG_INTERFACE'):
        summary_parts.append(f"container={DOCKER_CONTAINER_HINT}")
        summary_parts.append(f"interface={_detected_awg['WG_INTERFACE']}")
    if _detected_awg.get('SERVER_PUBLIC_KEY'):
        summary_parts.append('public_key=найден')
    if _detected_awg.get('DETECTED_HOST_PORT'):
        summary_parts.append(f"port={_detected_awg['DETECTED_HOST_PORT']}")
    if os.getenv('SERVER_IP', '').strip():
        summary_parts.append(f"endpoint={os.getenv('SERVER_IP').strip()}")
    logger.info('Автоопределение AWG: %s', ', '.join(summary_parts))

if sys.stdin and sys.stdin.isatty():
    for key, prompt in REQUIRED_PROMPTS.items():
        current = os.getenv(key, '').strip()
        if current:
            continue
        value = ''
        while not value:
            value = input(f'{prompt}: ').strip()
        _save_env_value(key, value)

    for key in (
        'ENCRYPTION_SECRET', 'PUBLIC_HOST', 'SERVER_HOST', 'SERVER_DOMAIN', 'DOCKER_CONTAINER', 'WG_INTERFACE', 'SERVER_PUBLIC_KEY', 'SERVER_IP',
        'SERVER_NAME', 'DOWNLOAD_URL', 'SUPPORT_USERNAME', 'VPN_SUBNET_PREFIX',
        'PRIMARY_DNS', 'SECONDARY_DNS', 'CLIENT_MTU', 'PERSISTENT_KEEPALIVE', 'CLIENT_ALLOWED_IPS',
        'AWG_JC', 'AWG_JMIN', 'AWG_JMAX', 'AWG_S1', 'AWG_S2', 'AWG_S3', 'AWG_S4',
        'AWG_H1', 'AWG_H2', 'AWG_H3', 'AWG_H4', 'AWG_I1', 'AWG_I2', 'AWG_I3', 'AWG_I4', 'AWG_I5', 'IGNORE_PEERS',
        'AWG_PROTOCOL_VERSION', 'AWG_TRANSPORT_PROTO', 'DB_PATH', 'FIRST_CLIENT_OCTET',
        'MAX_CLIENT_OCTET', 'CONFIGS_PER_USER', 'CLEANUP_INTERVAL_SECONDS',
        'STARS_PRICE_7_DAYS', 'STARS_PRICE_30_DAYS', 'PURCHASE_CLICK_COOLDOWN_SECONDS',
        'PURCHASE_RATE_LIMIT_TTL_SECONDS', 'ADMIN_COMMAND_COOLDOWN_SECONDS', 'DOCKER_RETRIES',
        'DOCKER_RETRY_BASE_DELAY', 'DOCKER_TIMEOUT_SECONDS', 'AWG_PEERS_CACHE_TTL_SECONDS',
    ):
        current = os.getenv(key, '').strip()
        if current:
            _save_env_value(key, current)
    load_dotenv(ENV_FILE, override=True)


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

SERVER_PUBLIC_KEY = os.getenv('SERVER_PUBLIC_KEY', '').strip()
SERVER_IP = os.getenv('SERVER_IP', '').strip()

DOCKER_CONTAINER = os.getenv('DOCKER_CONTAINER', DEFAULT_ENV['DOCKER_CONTAINER']).strip()
WG_INTERFACE = os.getenv('WG_INTERFACE', DEFAULT_ENV['WG_INTERFACE']).strip()
DB_PATH = os.getenv('DB_PATH', DEFAULT_ENV['DB_PATH']).strip()

DOWNLOAD_URL = os.getenv('DOWNLOAD_URL', DEFAULT_ENV['DOWNLOAD_URL']).strip()
SUPPORT_USERNAME = os.getenv('SUPPORT_USERNAME', DEFAULT_ENV['SUPPORT_USERNAME']).strip()
SERVER_NAME = os.getenv('SERVER_NAME', 'My VPN').strip()

STARS_PRICE_7_DAYS = env_int('STARS_PRICE_7_DAYS', int(DEFAULT_ENV['STARS_PRICE_7_DAYS']))
STARS_PRICE_30_DAYS = env_int('STARS_PRICE_30_DAYS', int(DEFAULT_ENV['STARS_PRICE_30_DAYS']))

VPN_SUBNET_PREFIX = os.getenv('VPN_SUBNET_PREFIX', DEFAULT_ENV['VPN_SUBNET_PREFIX']).strip()
FIRST_CLIENT_OCTET = env_int('FIRST_CLIENT_OCTET', int(DEFAULT_ENV['FIRST_CLIENT_OCTET']))
MAX_CLIENT_OCTET = env_int('MAX_CLIENT_OCTET', int(DEFAULT_ENV['MAX_CLIENT_OCTET']))
CONFIGS_PER_USER = env_int('CONFIGS_PER_USER', int(DEFAULT_ENV['CONFIGS_PER_USER']))
CLEANUP_INTERVAL_SECONDS = env_int('CLEANUP_INTERVAL_SECONDS', int(DEFAULT_ENV['CLEANUP_INTERVAL_SECONDS']))

PRIMARY_DNS = os.getenv('PRIMARY_DNS', DEFAULT_ENV['PRIMARY_DNS']).strip()
SECONDARY_DNS = os.getenv('SECONDARY_DNS', DEFAULT_ENV['SECONDARY_DNS']).strip()
CLIENT_MTU = os.getenv('CLIENT_MTU', DEFAULT_ENV['CLIENT_MTU']).strip()
PERSISTENT_KEEPALIVE = os.getenv('PERSISTENT_KEEPALIVE', DEFAULT_ENV['PERSISTENT_KEEPALIVE']).strip()
CLIENT_ALLOWED_IPS = os.getenv('CLIENT_ALLOWED_IPS', DEFAULT_ENV['CLIENT_ALLOWED_IPS']).strip()
ENCRYPTION_SECRET = os.getenv('ENCRYPTION_SECRET', '').strip()
IGNORE_PEERS = [p.strip() for p in os.getenv('IGNORE_PEERS', '').split(',') if p.strip()]

AWG_JC = os.getenv('AWG_JC', DEFAULT_ENV['AWG_JC']).strip()
AWG_JMIN = os.getenv('AWG_JMIN', DEFAULT_ENV['AWG_JMIN']).strip()
AWG_JMAX = os.getenv('AWG_JMAX', DEFAULT_ENV['AWG_JMAX']).strip()
AWG_S1 = os.getenv('AWG_S1', DEFAULT_ENV['AWG_S1']).strip()
AWG_S2 = os.getenv('AWG_S2', DEFAULT_ENV['AWG_S2']).strip()
AWG_S3 = os.getenv('AWG_S3', DEFAULT_ENV['AWG_S3']).strip()
AWG_S4 = os.getenv('AWG_S4', DEFAULT_ENV['AWG_S4']).strip()
AWG_H1 = os.getenv('AWG_H1', DEFAULT_ENV['AWG_H1']).strip()
AWG_H2 = os.getenv('AWG_H2', DEFAULT_ENV['AWG_H2']).strip()
AWG_H3 = os.getenv('AWG_H3', DEFAULT_ENV['AWG_H3']).strip()
AWG_H4 = os.getenv('AWG_H4', DEFAULT_ENV['AWG_H4']).strip()
AWG_I1 = os.getenv('AWG_I1', DEFAULT_ENV['AWG_I1']).strip()
AWG_I2 = os.getenv('AWG_I2', DEFAULT_ENV['AWG_I2']).strip()
AWG_I3 = os.getenv('AWG_I3', DEFAULT_ENV['AWG_I3']).strip()
AWG_I4 = os.getenv('AWG_I4', DEFAULT_ENV['AWG_I4']).strip()
AWG_I5 = os.getenv('AWG_I5', DEFAULT_ENV['AWG_I5']).strip()
AWG_PROTOCOL_VERSION = os.getenv('AWG_PROTOCOL_VERSION', DEFAULT_ENV['AWG_PROTOCOL_VERSION']).strip()
AWG_TRANSPORT_PROTO = os.getenv('AWG_TRANSPORT_PROTO', DEFAULT_ENV['AWG_TRANSPORT_PROTO']).strip()

PURCHASE_CLICK_COOLDOWN_SECONDS = env_int('PURCHASE_CLICK_COOLDOWN_SECONDS', int(DEFAULT_ENV['PURCHASE_CLICK_COOLDOWN_SECONDS']))
PURCHASE_RATE_LIMIT_TTL_SECONDS = env_int('PURCHASE_RATE_LIMIT_TTL_SECONDS', int(DEFAULT_ENV['PURCHASE_RATE_LIMIT_TTL_SECONDS']))
ADMIN_COMMAND_COOLDOWN_SECONDS = env_int('ADMIN_COMMAND_COOLDOWN_SECONDS', int(DEFAULT_ENV['ADMIN_COMMAND_COOLDOWN_SECONDS']))
DOCKER_RETRIES = env_int('DOCKER_RETRIES', int(DEFAULT_ENV['DOCKER_RETRIES']))
DOCKER_RETRY_BASE_DELAY = env_float('DOCKER_RETRY_BASE_DELAY', float(DEFAULT_ENV['DOCKER_RETRY_BASE_DELAY']))
DOCKER_TIMEOUT_SECONDS = env_int('DOCKER_TIMEOUT_SECONDS', int(DEFAULT_ENV['DOCKER_TIMEOUT_SECONDS']))
AWG_PEERS_CACHE_TTL_SECONDS = env_float('AWG_PEERS_CACHE_TTL_SECONDS', float(DEFAULT_ENV['AWG_PEERS_CACHE_TTL_SECONDS']))

required_missing = []
if not API_TOKEN:
    required_missing.append('API_TOKEN')
if ADMIN_ID <= 0:
    required_missing.append('ADMIN_ID')
if not SERVER_PUBLIC_KEY:
    required_missing.append('SERVER_PUBLIC_KEY (не удалось определить из docker exec awg show)')
if not SERVER_IP:
    required_missing.append('SERVER_IP (не удалось собрать из PUBLIC_HOST / внешнего IP / домена и порта контейнера)')
if not ENCRYPTION_SECRET:
    required_missing.append('ENCRYPTION_SECRET')
if required_missing:
    raise RuntimeError(
        'Не заданы переменные окружения: '
        + ', '.join(required_missing)
        + '. Запустите бота на том же сервере, где доступен docker и контейнер AmneziaWG, либо заполните .env вручную.'
    )
