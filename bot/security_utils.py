import base64
import hashlib
import os
from cryptography.fernet import Fernet, InvalidToken

from config import ENCRYPTION_SECRET, logger


def _derive_key(secret: str) -> bytes:
    material = secret
    digest = hashlib.sha256(material.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest)


_active_secret = ENCRYPTION_SECRET or os.getenv("ENCRYPTION_SECRET_FALLBACK", "")
if not _active_secret:
    raise RuntimeError("ENCRYPTION_SECRET не задан. Без него нельзя безопасно работать с конфигами.")
_FERNET = Fernet(_derive_key(_active_secret))
_OLD_SECRETS = [item.strip() for item in os.getenv("ENCRYPTION_OLD_SECRETS", "").split(",") if item.strip()]
_OLD_FERNETS = [Fernet(_derive_key(secret)) for secret in _OLD_SECRETS]


def encrypt_text(value: str | None) -> str:
    if not value:
        return ""
    token = _FERNET.encrypt(value.encode("utf-8")).decode("utf-8")
    return f"enc:v1:{token}"



def decrypt_text(value: str | None) -> str:
    if not value:
        return ""
    if not value.startswith("enc:"):
        return value
    raw = value
    if value.startswith("enc:v1:"):
        value = "enc:" + value.removeprefix("enc:v1:")
    try:
        return _FERNET.decrypt(value[4:].encode("utf-8")).decode("utf-8")
    except InvalidToken:
        for fallback in _OLD_FERNETS:
            try:
                return fallback.decrypt(value[4:].encode("utf-8")).decode("utf-8")
            except InvalidToken:
                continue
        logger.error("Не удалось расшифровать значение: invalid token (value=%s...)", raw[:24])
        raise RuntimeError("Ошибка расшифровки конфигурации. Требуется проверка ENCRYPTION_SECRET/ENCRYPTION_OLD_SECRETS.")
    except Exception as e:
        logger.error("Не удалось расшифровать значение: %s", e)
        raise RuntimeError("Ошибка расшифровки конфигурации.") from e
