import ipaddress
import os
import subprocess
import logging
import shutil

logger = logging.getLogger(__name__)


def command_exists(name: str) -> bool:
    # Avoid shell interpolation: name may come from runtime/env and must never be
    # evaluated by a shell command.
    return shutil.which(name) is not None


def run_local_command(args: list[str], timeout: int = 10) -> str:
    result = subprocess.run(args, check=False, capture_output=True, text=True, timeout=timeout)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or 'command failed')
    return result.stdout.strip()


def docker_available() -> bool:
    if not command_exists('docker'):
        return False
    try:
        run_local_command(['docker', 'ps'], timeout=8)
        return True
    except Exception:
        return False


def docker_exec(container: str, command: list[str], timeout: int = 10) -> str:
    return run_local_command(['docker', 'exec', '-i', container, *command], timeout=timeout)


def valid_container(name: str) -> bool:
    try:
        run_local_command(['docker', 'inspect', name], timeout=8)
        return True
    except Exception:
        return False


def find_awg_container(default_name: str) -> str:
    configured = os.getenv('DOCKER_CONTAINER', '').strip()
    if configured and docker_available() and valid_container(configured):
        return configured
    if not docker_available():
        return configured or default_name
    try:
        lines = run_local_command(['docker', 'ps', '--format', '{{.Names}}\t{{.Image}}'], timeout=8).splitlines()
    except Exception:
        return configured or default_name

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
    return configured or default_name


def is_public_ip(value: str) -> bool:
    try:
        addr = ipaddress.ip_address(value)
        return addr.version == 4 and not (addr.is_private or addr.is_loopback or addr.is_link_local or addr.is_multicast or addr.is_unspecified)
    except ValueError:
        return False


def resolve_public_ipv4(value: str) -> str:
    value = value.strip()
    if not value:
        return ''
    if is_public_ip(value):
        return value
    return ''


def detect_public_host() -> str:
    direct = resolve_public_ipv4(os.getenv('PUBLIC_HOST', '').strip())
    if direct:
        return direct

    if command_exists('curl'):
        for url in ('https://api.ipify.org', 'https://ifconfig.me/ip', 'https://ipv4.icanhazip.com'):
            try:
                value = run_local_command(['curl', '-4', '-fsSL', url], timeout=8).strip()
                if is_public_ip(value):
                    return value
            except Exception:
                continue
    return ''


def parse_subnet_prefix(show_output: str) -> str:
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


def detect_awg_from_container(container: str, interface_hint: str) -> dict[str, str]:
    detected: dict[str, str] = {}
    if not container or not docker_available() or not valid_container(container):
        return detected

    show_output = ''
    last_error: Exception | None = None
    for cmd in ([['awg', 'show', interface_hint], ['awg', 'show']]):
        try:
            show_output = docker_exec(container, cmd, timeout=12)
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

    subnet_prefix = parse_subnet_prefix(show_output)
    if subnet_prefix:
        detected['VPN_SUBNET_PREFIX'] = subnet_prefix

    port_value = detected.get('DETECTED_AWG_PORT', '').strip()
    if port_value:
        try:
            host_port_output = run_local_command(['docker', 'port', container, f'{port_value}/udp'], timeout=10)
            first_line = host_port_output.strip().splitlines()[0]
            host_port = first_line.rsplit(':', 1)[-1].strip()
            if host_port:
                detected['DETECTED_HOST_PORT'] = host_port
        except Exception:
            detected['DETECTED_HOST_PORT'] = port_value

    return detected
