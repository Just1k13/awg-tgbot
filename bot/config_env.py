import os
from pathlib import Path

from dotenv import load_dotenv

ENV_FILE = Path('.env')
load_dotenv(ENV_FILE)


def read_env_file(path: Path) -> dict[str, str]:
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
