from __future__ import annotations

import base64
import hashlib
import hmac
import secrets as py_secrets
import time
from typing import Any, Dict, Optional

from fastapi import Request

from app.config import settings


COOKIE_NAME = "game_daily_session"
PASSWORD_ITERATIONS = 210_000
ROLE_LEVELS = {
    "viewer": 1,
    "editor": 2,
    "admin": 3,
}


def auth_enabled() -> bool:
    return bool(settings.auth_required or settings.admin_password)


def _signing_key() -> bytes:
    key = settings.app_secret_key or settings.admin_password
    return key.encode("utf-8")


def _signature(payload: str) -> str:
    digest = hmac.new(_signing_key(), payload.encode("utf-8"), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def create_session_token(username: str, session_version: int = 0) -> str:
    expires_at = int(time.time() + settings.admin_session_hours * 3600)
    nonce = py_secrets.token_urlsafe(16)
    payload = f"{username}:{expires_at}:{nonce}:{session_version}"
    return f"{payload}:{_signature(payload)}"


def hash_password(password: str) -> str:
    salt = py_secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt,
        PASSWORD_ITERATIONS,
    )
    return "$".join(
        [
            "pbkdf2_sha256",
            str(PASSWORD_ITERATIONS),
            base64.urlsafe_b64encode(salt).decode("ascii").rstrip("="),
            base64.urlsafe_b64encode(digest).decode("ascii").rstrip("="),
        ]
    )


def verify_password(password: str, password_hash: str) -> bool:
    try:
        algorithm, iterations_raw, salt_raw, digest_raw = password_hash.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        iterations = int(iterations_raw)
        salt = base64.urlsafe_b64decode(salt_raw + "=" * (-len(salt_raw) % 4))
        expected = base64.urlsafe_b64decode(digest_raw + "=" * (-len(digest_raw) % 4))
    except Exception:
        return False

    received = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return hmac.compare_digest(received, expected)


def normalize_role(role: str) -> str:
    return role if role in ROLE_LEVELS else "viewer"


def has_role(user_role: str, required_role: str) -> bool:
    return ROLE_LEVELS.get(normalize_role(user_role), 0) >= ROLE_LEVELS.get(
        normalize_role(required_role),
        0,
    )


def _env_admin_user(username: str) -> Optional[Dict[str, Any]]:
    username = username.strip()
    if not settings.admin_password:
        return None
    if not hmac.compare_digest(username, settings.admin_username):
        return None
    return {
        "id": 0,
        "username": settings.admin_username,
        "role": "admin",
        "enabled": 1,
        "session_version": 0,
        "source": "env",
    }


def _database_user(username: str) -> Optional[Dict[str, Any]]:
    username = username.strip()
    try:
        from app.db import get_auth_user_by_username

        user = get_auth_user_by_username(username)
    except Exception:
        user = None
    if not user or not int(user.get("enabled", 0)):
        return None
    user["role"] = normalize_role(str(user.get("role") or "viewer"))
    user["source"] = "database"
    return user


def get_session_user(username: str) -> Optional[Dict[str, Any]]:
    return _database_user(username) or _env_admin_user(username)


def verify_session_token(token: str) -> Optional[str]:
    parts = token.split(":")
    if len(parts) != 5:
        return None

    username, expires_at_raw, nonce, version_raw, received_signature = parts
    if not username or not expires_at_raw or not nonce or not version_raw or not received_signature:
        return None

    try:
        expires_at = int(expires_at_raw)
        token_version = int(version_raw)
    except ValueError:
        return None

    if expires_at < int(time.time()):
        return None

    payload = f"{username}:{expires_at}:{nonce}:{token_version}"
    expected_signature = _signature(payload)
    if not hmac.compare_digest(received_signature, expected_signature):
        return None

    user = get_session_user(username)
    if user is None:
        return None
    # session_version 不匹配说明密码已修改或会话已被吊销，旧 token 立即失效。
    user_version = int(user.get("session_version") or 0)
    if token_version != user_version:
        return None
    return username


def authenticate_user(username: str, password: str) -> Optional[Dict[str, Any]]:
    username = username.strip()
    if not auth_enabled():
        return {
            "id": 0,
            "username": settings.admin_username,
            "role": "admin",
            "enabled": 1,
            "source": "disabled",
        }

    user = _database_user(username)
    if user and verify_password(password, str(user.get("password_hash") or "")):
        return user

    env_user = _env_admin_user(username)
    if env_user and hmac.compare_digest(password, settings.admin_password):
        return env_user

    return None


def authenticate(username: str, password: str) -> bool:
    return authenticate_user(username, password) is not None


def current_user(request: Request) -> Optional[Dict[str, Any]]:
    if not auth_enabled():
        return {
            "id": 0,
            "username": settings.admin_username,
            "role": "admin",
            "enabled": 1,
            "source": "disabled",
        }
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        return None
    username = verify_session_token(token)
    if not username:
        return None
    return get_session_user(username)


def current_username(request: Request) -> Optional[str]:
    user = current_user(request)
    return str(user["username"]) if user else None


def current_role(request: Request) -> Optional[str]:
    user = current_user(request)
    return str(user["role"]) if user else None


def is_authenticated(request: Request) -> bool:
    return current_user(request) is not None
