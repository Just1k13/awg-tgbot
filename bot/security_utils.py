import base64
import hashlib
from cryptography.fernet import Fernet, InvalidToken

from config import ENCRYPTION_SECRET, logger


def _derive_key() -> bytes:
    material = ENCRYPTION_SECRET
    digest = hashlib.sha256(material.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest)


_FERNET = Fernet(_derive_key())


def encrypt_text(value: str | None) -> str:
    if not value:
        return ""
    token = _FERNET.encrypt(value.encode("utf-8")).decode("utf-8")
    return f"enc:{token}"



def decrypt_text(value: str | None) -> str:
    if not value:
        return ""
    if not value.startswith("enc:"):
        return value
    try:
        return _FERNET.decrypt(value[4:].encode("utf-8")).decode("utf-8")
    except InvalidToken:
        logger.error("Не удалось расшифровать значение: invalid token")
        return ""
    except Exception as e:
        logger.error("Не удалось расшифровать значение: %s", e)
        return ""
