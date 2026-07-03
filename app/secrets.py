from __future__ import annotations

import base64
import hashlib

try:
    from cryptography.fernet import Fernet
except ModuleNotFoundError:  # pragma: no cover - development fallback only
    Fernet = None  # type: ignore[assignment]

from app.config import settings


def _fernet_key() -> bytes:
    if settings.app_secret_key:
        seed = settings.app_secret_key.encode("utf-8")
    else:
        seed = f"dev:{settings.project_root}:{settings.mysql_database}".encode("utf-8")
    digest = hashlib.sha256(seed).digest()
    return base64.urlsafe_b64encode(digest)


def encrypt_secret(value: str) -> str:
    if not value:
        return ""
    if Fernet is None:
        return "b64:" + base64.urlsafe_b64encode(value.encode("utf-8")).decode("utf-8")
    return "fernet:" + Fernet(_fernet_key()).encrypt(value.encode("utf-8")).decode("utf-8")


def decrypt_secret(value: str) -> str:
    if not value:
        return ""
    if value.startswith("b64:"):
        return base64.urlsafe_b64decode(value[4:].encode("utf-8")).decode("utf-8")
    token = value[7:] if value.startswith("fernet:") else value
    if Fernet is None:
        raise RuntimeError("cryptography is required to decrypt this API key")
    return Fernet(_fernet_key()).decrypt(token.encode("utf-8")).decode("utf-8")


def mask_secret(value: str) -> str:
    if not value:
        return ""
    if len(value) <= 8:
        return "****"
    return f"{value[:4]}****{value[-4:]}"
