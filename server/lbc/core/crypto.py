"""Verschlüsselung von Geheimnissen (Tokens, Passwörter) in der Datenbank.

Schlüssel wird aus APP_SECRET_KEY (.env) abgeleitet. Ohne APP_SECRET_KEY startet die Anwendung nicht.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets

from cryptography.fernet import Fernet, InvalidToken

from .config import get_settings


class SecretKeyMissing(RuntimeError):
    pass


def _fernet() -> Fernet:
    key = get_settings().app_secret_key
    if not key or len(key) < 32:
        raise SecretKeyMissing("APP_SECRET_KEY fehlt oder ist zu kurz (mindestens 32 Zeichen).")
    digest = hashlib.sha256(("lbc-secrets:" + key).encode()).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt(value: str) -> str:
    return _fernet().encrypt(value.encode()).decode()


def decrypt(token: str) -> str:
    try:
        return _fernet().decrypt(token.encode()).decode()
    except InvalidToken as exc:
        raise ValueError("Gespeichertes Geheimnis kann nicht entschlüsselt werden (APP_SECRET_KEY geändert?)") from exc


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def new_token(nbytes: int = 32) -> str:
    return secrets.token_urlsafe(nbytes)


def constant_time_equals(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode(), b.encode())
