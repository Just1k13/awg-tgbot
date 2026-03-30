import os
from pathlib import Path

from dotenv import load_dotenv

ENV_FILE = Path('.env')


def _read_env_text_lossy(path: Path) -> tuple[str, bool] | None:
    if not path.exists():
        return None
    raw = path.read_bytes()
    for encoding in ('utf-8', 'utf-8-sig'):
        try:
            return raw.decode(encoding), False
        except UnicodeDecodeError:
            continue
    for encoding in ('cp1251', 'latin-1'):
        try:
            return raw.decode(encoding), True
        except UnicodeDecodeError:
            continue
    return raw.decode('utf-8', errors='replace'), True


def _ensure_env_utf8(path: Path) -> None:
    result = _read_env_text_lossy(path)
    if result is None:
        return
    text, should_rewrite = result
    normalized = text.replace('\x00', '')
    if should_rewrite or normalized != text:
        path.write_text(normalized, encoding='utf-8')


_ensure_env_utf8(ENV_FILE)
load_dotenv(ENV_FILE, encoding='utf-8')


def read_env_file(path: Path) -> dict[str, str]:
    data: dict[str, str] = {}
    result = _read_env_text_lossy(path)
    if result is None:
        return data
    text, _ = result
    for raw_line in text.replace('\x00', '').splitlines():
        line = raw_line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, value = line.split('=', 1)
        data[key.strip()] = value.strip()
    return data


_existing_env = read_env_file(ENV_FILE)


def save_env_value_raw(name: str, value: str) -> None:
    _existing_env[name] = value
    content = '\n'.join(f'{key}={val}' for key, val in sorted(_existing_env.items())) + '\n'
    ENV_FILE.write_text(content, encoding='utf-8')
    os.environ[name] = value


def env_with_runtime_default(name: str, default: str) -> str:
    value = os.getenv(name, '').strip()
    if value:
        return value
    return default


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
