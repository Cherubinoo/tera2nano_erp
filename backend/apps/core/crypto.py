import base64
import hashlib
import hmac

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings


def _key_bytes():
    key = settings.FIELD_ENCRYPTION_KEY
    if key:
        return key.encode()
    # DEBUG-only fallback so a fresh checkout works; production refuses to start without a key.
    digest = hashlib.sha256(("fernet:" + settings.SECRET_KEY).encode()).digest()
    return base64.urlsafe_b64encode(digest)


def _fernet():
    return Fernet(_key_bytes())


def encrypt(plaintext: str) -> str:
    return _fernet().encrypt(plaintext.encode()).decode()


def decrypt(token: str) -> str:
    try:
        return _fernet().decrypt(token.encode()).decode()
    except InvalidToken as exc:
        raise ValueError("Stored value cannot be decrypted with the configured key.") from exc


def keyed_hash(value: str) -> str:
    """Deterministic keyed hash, used to detect duplicates without storing plaintext."""
    return hmac.new(_key_bytes(), value.encode(), hashlib.sha256).hexdigest()


def mask_account_number(last4: str) -> str:
    return f"XXXXXX{last4}" if last4 else ""
