import base64
import hashlib
import os
from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

from config import ENCRYPTION_SECRET, logger


PBKDF2_ITERATIONS = int(os.getenv("ENCRYPTION_PBKDF2_ITERATIONS", "390000"))
V2_SALT_SIZE = 16


def _derive_key_v1(secret: str) -> bytes:
    digest = hashlib.sha256(secret.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest)


def _derive_key_v2(secret: str, salt: bytes) -> bytes:
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=PBKDF2_ITERATIONS,
    )
    return base64.urlsafe_b64encode(kdf.derive(secret.encode("utf-8")))


_active_secret = ENCRYPTION_SECRET or os.getenv("ENCRYPTION_SECRET_FALLBACK", "")
if not _active_secret:
    raise RuntimeError("ENCRYPTION_SECRET не задан. Без него нельзя безопасно работать с конфигами.")
_OLD_SECRETS = [item.strip() for item in os.getenv("ENCRYPTION_OLD_SECRETS", "").split(",") if item.strip()]
_V1_ACTIVE_FERNET = Fernet(_derive_key_v1(_active_secret))
_V1_OLD_FERNETS = [Fernet(_derive_key_v1(secret)) for secret in _OLD_SECRETS]


def encrypt_text(value: str | None) -> str:
    if not value:
        return ""
    salt = os.urandom(V2_SALT_SIZE)
    token = Fernet(_derive_key_v2(_active_secret, salt)).encrypt(value.encode("utf-8")).decode("utf-8")
    return f"enc:v2:{base64.urlsafe_b64encode(salt).decode('ascii')}:{token}"


def _decrypt_v1(token: str) -> str:
    try:
        return _V1_ACTIVE_FERNET.decrypt(token.encode("utf-8")).decode("utf-8")
    except InvalidToken:
        for fallback in _V1_OLD_FERNETS:
            try:
                return fallback.decrypt(token.encode("utf-8")).decode("utf-8")
            except InvalidToken:
                continue
    raise RuntimeError("invalid_v1_token")


def _decrypt_v2(salt_b64: str, token: str) -> str:
    salt = base64.urlsafe_b64decode(salt_b64.encode("ascii"))
    for secret in [_active_secret, *_OLD_SECRETS]:
        try:
            fernet = Fernet(_derive_key_v2(secret, salt))
            return fernet.decrypt(token.encode("utf-8")).decode("utf-8")
        except InvalidToken:
            continue
    raise RuntimeError("invalid_v2_token")


def decrypt_text(value: str | None) -> str:
    if not value:
        return ""
    if not value.startswith("enc:"):
        return value
    try:
        if value.startswith("enc:v2:"):
            _, _, salt_b64, token = value.split(":", 3)
            return _decrypt_v2(salt_b64, token)
        if value.startswith("enc:v1:"):
            token = value.removeprefix("enc:v1:")
            return _decrypt_v1(token)
        token = value.removeprefix("enc:")
        return _decrypt_v1(token)
    except Exception as e:
        err_type = type(e).__name__
        logger.error("Не удалось расшифровать значение: invalid token (error_type=%s)", err_type)
        raise RuntimeError("Ошибка расшифровки конфигурации. Требуется проверка ENCRYPTION_SECRET/ENCRYPTION_OLD_SECRETS.")
