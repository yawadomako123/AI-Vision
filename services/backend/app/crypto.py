"""Camera stream URLs carry credentials: encrypted at rest, masked in API responses."""

from urllib.parse import urlsplit, urlunsplit

from cryptography.fernet import Fernet

from app.config import settings

_fernet = Fernet(settings.camera_secret_key.encode())


def encrypt(value: str) -> str:
    return _fernet.encrypt(value.encode()).decode()


def decrypt(token: str) -> str:
    return _fernet.decrypt(token.encode()).decode()


def mask_url(url: str) -> str:
    """rtsp://admin:secret@192.168.1.5:554/s1 -> rtsp://admin:***@192.168.1.5:554/s1"""
    parts = urlsplit(url)
    if parts.password is None:
        return url
    host = parts.hostname or ""
    if parts.port:
        host = f"{host}:{parts.port}"
    netloc = f"{parts.username}:***@{host}" if parts.username else f"***@{host}"
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))
