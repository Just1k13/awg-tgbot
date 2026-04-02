import logging
import os

from config_defaults import DEFAULT_ENV
from config_detect import (
    detect_awg_from_container,
    detect_public_host,
    find_awg_container,
    resolve_public_ipv4,
)
from config_env import env_float, env_int, env_with_runtime_default, save_env_value_raw
from config_validate import validate_helper_policy, validate_required_env
from config_validate import validate_awg_obfuscation_settings, validate_client_allowed_ips, validate_persistent_keepalive

logging.basicConfig(level=logging.INFO, format='%(asctime)s | %(levelname)s | %(message)s')
logger = logging.getLogger(__name__)


def save_env_value(name: str, value: str | int) -> None:
    save_env_value_raw(name, str(value))
    globals()[name] = value


STARS_PRICE_KEYS = ("STARS_PRICE_7_DAYS", "STARS_PRICE_30_DAYS", "STARS_PRICE_90_DAYS")


def set_stars_price(name: str, value: int) -> tuple[int, int]:
    if name not in STARS_PRICE_KEYS:
        raise ValueError("unknown_stars_price_key")
    if value <= 0:
        raise ValueError("stars_price_must_be_positive")
    old_value = int(globals()[name])
    save_env_value(name, value)
    return old_value, value


def get_support_username() -> str:
    return globals().get('SUPPORT_USERNAME', '').strip()


def get_download_url() -> str:
    return globals().get('DOWNLOAD_URL', '').strip() or DEFAULT_ENV['DOWNLOAD_URL']


def maybe_set_support_username(username: str | None) -> str:
    if not username:
        return get_support_username()
    normalized = username if username.startswith('@') else f'@{username}'
    globals()['SUPPORT_USERNAME'] = normalized
    return normalized


AUTO_DETECT_ON_IMPORT = os.getenv('CONFIG_AUTODETECT_ON_IMPORT', '0').strip() == '1'
if AUTO_DETECT_ON_IMPORT:
    DOCKER_CONTAINER_HINT = find_awg_container(DEFAULT_ENV['DOCKER_CONTAINER'])
    WG_INTERFACE_HINT = env_with_runtime_default('WG_INTERFACE', DEFAULT_ENV['WG_INTERFACE'])
    _detected_awg = detect_awg_from_container(DOCKER_CONTAINER_HINT, WG_INTERFACE_HINT)
    PUBLIC_HOST_HINT = env_with_runtime_default('PUBLIC_HOST', detect_public_host())
else:
    DOCKER_CONTAINER_HINT = env_with_runtime_default('DOCKER_CONTAINER', DEFAULT_ENV['DOCKER_CONTAINER'])
    WG_INTERFACE_HINT = env_with_runtime_default('WG_INTERFACE', DEFAULT_ENV['WG_INTERFACE'])
    _detected_awg = {}
    PUBLIC_HOST_HINT = env_with_runtime_default('PUBLIC_HOST', DEFAULT_ENV['PUBLIC_HOST'])
_raw_public_host = os.getenv('PUBLIC_HOST', '').strip()
PUBLIC_HOST_HINT = resolve_public_ipv4(PUBLIC_HOST_HINT)
_public_host_error = ''
if _raw_public_host and not PUBLIC_HOST_HINT:
    _public_host_error = 'PUBLIC_HOST (ожидается публичный IPv4 без порта)'
    logger.warning('PUBLIC_HOST задан некорректно: %r', _raw_public_host)
SERVER_NAME_HINT = os.getenv('SERVER_NAME', DEFAULT_ENV['SERVER_NAME']).strip() or DEFAULT_ENV['SERVER_NAME']
SERVER_PUBLIC_KEY_HINT = env_with_runtime_default('SERVER_PUBLIC_KEY', _detected_awg.get('SERVER_PUBLIC_KEY', '').strip())
DETECTED_HOST_PORT_HINT = _detected_awg.get('DETECTED_HOST_PORT', '').strip()

_raw_server_ip = os.getenv('SERVER_IP', '').strip()
SERVER_IP_HINT = ''
_server_ip_error = ''
if _raw_server_ip:
    if ':' in _raw_server_ip:
        raw_host, raw_port = _raw_server_ip.rsplit(':', 1)
        resolved_host = resolve_public_ipv4(raw_host)
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


API_TOKEN = os.getenv('API_TOKEN', '').strip()
ADMIN_ID = env_int('ADMIN_ID', 0)

SERVER_PUBLIC_KEY = SERVER_PUBLIC_KEY_HINT
SERVER_IP = SERVER_IP_HINT
PUBLIC_HOST = PUBLIC_HOST_HINT

DOCKER_CONTAINER = env_with_runtime_default('DOCKER_CONTAINER', DOCKER_CONTAINER_HINT or DEFAULT_ENV['DOCKER_CONTAINER'])
WG_INTERFACE = env_with_runtime_default('WG_INTERFACE', _detected_awg.get('WG_INTERFACE', '').strip() or DEFAULT_ENV['WG_INTERFACE'])
DB_PATH = env_with_runtime_default('DB_PATH', DEFAULT_ENV['DB_PATH'])

DOWNLOAD_URL = env_with_runtime_default('DOWNLOAD_URL', DEFAULT_ENV['DOWNLOAD_URL'])
SUPPORT_USERNAME = env_with_runtime_default('SUPPORT_USERNAME', DEFAULT_ENV['SUPPORT_USERNAME'])
SERVER_NAME = SERVER_NAME_HINT

STARS_PRICE_7_DAYS = env_int('STARS_PRICE_7_DAYS', int(DEFAULT_ENV['STARS_PRICE_7_DAYS']))
STARS_PRICE_30_DAYS = env_int('STARS_PRICE_30_DAYS', int(DEFAULT_ENV['STARS_PRICE_30_DAYS']))
STARS_PRICE_90_DAYS = env_int('STARS_PRICE_90_DAYS', int(DEFAULT_ENV['STARS_PRICE_90_DAYS']))

VPN_SUBNET_PREFIX = env_with_runtime_default('VPN_SUBNET_PREFIX', _detected_awg.get('VPN_SUBNET_PREFIX', '').strip() or DEFAULT_ENV['VPN_SUBNET_PREFIX'])
FIRST_CLIENT_OCTET = env_int('FIRST_CLIENT_OCTET', int(DEFAULT_ENV['FIRST_CLIENT_OCTET']))
MAX_CLIENT_OCTET = env_int('MAX_CLIENT_OCTET', int(DEFAULT_ENV['MAX_CLIENT_OCTET']))
CONFIGS_PER_USER = env_int('CONFIGS_PER_USER', int(DEFAULT_ENV['CONFIGS_PER_USER']))
CLEANUP_INTERVAL_SECONDS = env_int('CLEANUP_INTERVAL_SECONDS', int(DEFAULT_ENV['CLEANUP_INTERVAL_SECONDS']))

PRIMARY_DNS = env_with_runtime_default('PRIMARY_DNS', DEFAULT_ENV['PRIMARY_DNS'])
SECONDARY_DNS = env_with_runtime_default('SECONDARY_DNS', DEFAULT_ENV['SECONDARY_DNS'])
CLIENT_MTU = env_with_runtime_default('CLIENT_MTU', DEFAULT_ENV['CLIENT_MTU'])
PERSISTENT_KEEPALIVE = env_with_runtime_default('PERSISTENT_KEEPALIVE', DEFAULT_ENV['PERSISTENT_KEEPALIVE'])
CLIENT_ALLOWED_IPS = env_with_runtime_default('CLIENT_ALLOWED_IPS', DEFAULT_ENV['CLIENT_ALLOWED_IPS'])
ENCRYPTION_SECRET = os.getenv('ENCRYPTION_SECRET', '').strip()
IGNORE_PEERS = [p.strip() for p in os.getenv('IGNORE_PEERS', DEFAULT_ENV['IGNORE_PEERS']).split(',') if p.strip()]

AWG_JC = env_with_runtime_default('AWG_JC', _detected_awg.get('AWG_JC', '').strip() or DEFAULT_ENV['AWG_JC'])
AWG_JMIN = env_with_runtime_default('AWG_JMIN', _detected_awg.get('AWG_JMIN', '').strip() or DEFAULT_ENV['AWG_JMIN'])
AWG_JMAX = env_with_runtime_default('AWG_JMAX', _detected_awg.get('AWG_JMAX', '').strip() or DEFAULT_ENV['AWG_JMAX'])
AWG_S1 = env_with_runtime_default('AWG_S1', _detected_awg.get('AWG_S1', '').strip() or DEFAULT_ENV['AWG_S1'])
AWG_S2 = env_with_runtime_default('AWG_S2', _detected_awg.get('AWG_S2', '').strip() or DEFAULT_ENV['AWG_S2'])
AWG_S3 = env_with_runtime_default('AWG_S3', _detected_awg.get('AWG_S3', '').strip() or DEFAULT_ENV['AWG_S3'])
AWG_S4 = env_with_runtime_default('AWG_S4', _detected_awg.get('AWG_S4', '').strip() or DEFAULT_ENV['AWG_S4'])
AWG_H1 = env_with_runtime_default('AWG_H1', _detected_awg.get('AWG_H1', '').strip() or DEFAULT_ENV['AWG_H1'])
AWG_H2 = env_with_runtime_default('AWG_H2', _detected_awg.get('AWG_H2', '').strip() or DEFAULT_ENV['AWG_H2'])
AWG_H3 = env_with_runtime_default('AWG_H3', _detected_awg.get('AWG_H3', '').strip() or DEFAULT_ENV['AWG_H3'])
AWG_H4 = env_with_runtime_default('AWG_H4', _detected_awg.get('AWG_H4', '').strip() or DEFAULT_ENV['AWG_H4'])
AWG_I1 = env_with_runtime_default('AWG_I1', DEFAULT_ENV['AWG_I1'])
AWG_I2 = env_with_runtime_default('AWG_I2', DEFAULT_ENV['AWG_I2'])
AWG_I3 = env_with_runtime_default('AWG_I3', DEFAULT_ENV['AWG_I3'])
AWG_I4 = env_with_runtime_default('AWG_I4', DEFAULT_ENV['AWG_I4'])
AWG_I5 = env_with_runtime_default('AWG_I5', DEFAULT_ENV['AWG_I5'])
AWG_PROTOCOL_VERSION = env_with_runtime_default('AWG_PROTOCOL_VERSION', DEFAULT_ENV['AWG_PROTOCOL_VERSION'])
AWG_TRANSPORT_PROTO = env_with_runtime_default('AWG_TRANSPORT_PROTO', DEFAULT_ENV['AWG_TRANSPORT_PROTO'])

PURCHASE_CLICK_COOLDOWN_SECONDS = env_int('PURCHASE_CLICK_COOLDOWN_SECONDS', int(DEFAULT_ENV['PURCHASE_CLICK_COOLDOWN_SECONDS']))
PURCHASE_RATE_LIMIT_TTL_SECONDS = env_int('PURCHASE_RATE_LIMIT_TTL_SECONDS', int(DEFAULT_ENV['PURCHASE_RATE_LIMIT_TTL_SECONDS']))
USER_REISSUE_COOLDOWN_SECONDS = env_int('USER_REISSUE_COOLDOWN_SECONDS', int(DEFAULT_ENV['USER_REISSUE_COOLDOWN_SECONDS']))
ADMIN_COMMAND_COOLDOWN_SECONDS = env_int('ADMIN_COMMAND_COOLDOWN_SECONDS', int(DEFAULT_ENV['ADMIN_COMMAND_COOLDOWN_SECONDS']))
DOCKER_RETRIES = env_int('DOCKER_RETRIES', int(DEFAULT_ENV['DOCKER_RETRIES']))
DOCKER_RETRY_BASE_DELAY = env_float('DOCKER_RETRY_BASE_DELAY', float(DEFAULT_ENV['DOCKER_RETRY_BASE_DELAY']))
DOCKER_TIMEOUT_SECONDS = env_int('DOCKER_TIMEOUT_SECONDS', int(DEFAULT_ENV['DOCKER_TIMEOUT_SECONDS']))
AWG_HELPER_PATH = env_with_runtime_default('AWG_HELPER_PATH', DEFAULT_ENV['AWG_HELPER_PATH'])
AWG_HELPER_USE_SUDO = env_int('AWG_HELPER_USE_SUDO', int(DEFAULT_ENV['AWG_HELPER_USE_SUDO'])) == 1
AWG_HELPER_POLICY_PATH = env_with_runtime_default('AWG_HELPER_POLICY_PATH', DEFAULT_ENV['AWG_HELPER_POLICY_PATH'])
AWG_PEERS_CACHE_TTL_SECONDS = env_float('AWG_PEERS_CACHE_TTL_SECONDS', float(DEFAULT_ENV['AWG_PEERS_CACHE_TTL_SECONDS']))
PENDING_KEY_TTL_SECONDS = env_int('PENDING_KEY_TTL_SECONDS', int(DEFAULT_ENV['PENDING_KEY_TTL_SECONDS']))
PAYMENT_RETRY_DELAY_SECONDS = env_int('PAYMENT_RETRY_DELAY_SECONDS', int(DEFAULT_ENV['PAYMENT_RETRY_DELAY_SECONDS']))
PAYMENT_PROVISIONING_LEASE_SECONDS = env_int('PAYMENT_PROVISIONING_LEASE_SECONDS', int(DEFAULT_ENV['PAYMENT_PROVISIONING_LEASE_SECONDS']))
PAYMENT_MAX_ATTEMPTS = env_int('PAYMENT_MAX_ATTEMPTS', int(DEFAULT_ENV['PAYMENT_MAX_ATTEMPTS']))
BROADCAST_BATCH_SIZE = env_int('BROADCAST_BATCH_SIZE', int(DEFAULT_ENV['BROADCAST_BATCH_SIZE']))
BROADCAST_BATCH_DELAY_SECONDS = env_float('BROADCAST_BATCH_DELAY_SECONDS', float(DEFAULT_ENV['BROADCAST_BATCH_DELAY_SECONDS']))
RECONCILIATION_INTERVAL_SECONDS = env_int('RECONCILIATION_INTERVAL_SECONDS', int(DEFAULT_ENV['RECONCILIATION_INTERVAL_SECONDS']))
BACKUP_SECURE_MODE = env_int('BACKUP_SECURE_MODE', int(DEFAULT_ENV['BACKUP_SECURE_MODE'])) == 1
BACKUP_ALLOW_INSECURE_SEND = env_int('BACKUP_ALLOW_INSECURE_SEND', int(DEFAULT_ENV['BACKUP_ALLOW_INSECURE_SEND'])) == 1
BACKUP_ENCRYPTION_KEY = env_with_runtime_default('BACKUP_ENCRYPTION_KEY', DEFAULT_ENV['BACKUP_ENCRYPTION_KEY'])
QOS_ENABLED = env_int('QOS_ENABLED', int(DEFAULT_ENV['QOS_ENABLED'])) == 1
DEFAULT_KEY_RATE_MBIT = env_int('DEFAULT_KEY_RATE_MBIT', int(DEFAULT_ENV['DEFAULT_KEY_RATE_MBIT']))
QOS_STRICT = env_int('QOS_STRICT', int(DEFAULT_ENV['QOS_STRICT'])) == 1
REFERRAL_ENABLED = env_int('REFERRAL_ENABLED', int(DEFAULT_ENV['REFERRAL_ENABLED'])) == 1
REFERRAL_INVITEE_BONUS_DAYS = env_int('REFERRAL_INVITEE_BONUS_DAYS', int(DEFAULT_ENV['REFERRAL_INVITEE_BONUS_DAYS']))
REFERRAL_INVITER_BONUS_DAYS = env_int('REFERRAL_INVITER_BONUS_DAYS', int(DEFAULT_ENV['REFERRAL_INVITER_BONUS_DAYS']))
EGRESS_DENYLIST_ENABLED = env_int('EGRESS_DENYLIST_ENABLED', int(DEFAULT_ENV['EGRESS_DENYLIST_ENABLED'])) == 1
EGRESS_DENYLIST_DOMAINS = env_with_runtime_default('EGRESS_DENYLIST_DOMAINS', DEFAULT_ENV['EGRESS_DENYLIST_DOMAINS'])
EGRESS_DENYLIST_CIDRS = env_with_runtime_default('EGRESS_DENYLIST_CIDRS', DEFAULT_ENV['EGRESS_DENYLIST_CIDRS'])
EGRESS_DENYLIST_REFRESH_MINUTES = env_int('EGRESS_DENYLIST_REFRESH_MINUTES', int(DEFAULT_ENV['EGRESS_DENYLIST_REFRESH_MINUTES']))
EGRESS_DENYLIST_MODE = env_with_runtime_default('EGRESS_DENYLIST_MODE', DEFAULT_ENV['EGRESS_DENYLIST_MODE'])
TORRENT_POLICY_TEXT_ENABLED = env_int('TORRENT_POLICY_TEXT_ENABLED', int(DEFAULT_ENV['TORRENT_POLICY_TEXT_ENABLED'])) == 1

validate_required_env(
    api_token=API_TOKEN,
    admin_id=ADMIN_ID,
    server_public_key=SERVER_PUBLIC_KEY,
    server_ip=SERVER_IP,
    encryption_secret=ENCRYPTION_SECRET,
    server_ip_error=_server_ip_error,
    public_host_error=_public_host_error,
)

validate_helper_policy(
    policy_path=AWG_HELPER_POLICY_PATH,
    docker_container=DOCKER_CONTAINER,
    wg_interface=WG_INTERFACE,
    logger=logger,
)

validate_awg_obfuscation_settings(
    awg_jc=AWG_JC,
    awg_jmin=AWG_JMIN,
    awg_jmax=AWG_JMAX,
    awg_s1=AWG_S1,
    awg_s2=AWG_S2,
    awg_s3=AWG_S3,
    awg_s4=AWG_S4,
    awg_h1=AWG_H1,
    awg_h2=AWG_H2,
    awg_h3=AWG_H3,
    awg_h4=AWG_H4,
    awg_i1=AWG_I1,
    awg_i2=AWG_I2,
    awg_i3=AWG_I3,
    awg_i4=AWG_I4,
    awg_i5=AWG_I5,
)

PERSISTENT_KEEPALIVE = validate_persistent_keepalive(PERSISTENT_KEEPALIVE)
CLIENT_ALLOWED_IPS = validate_client_allowed_ips(CLIENT_ALLOWED_IPS)
