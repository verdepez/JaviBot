import base64
import hashlib
import hmac
from functools import lru_cache

from cryptography.fernet import Fernet

from app.core.config import settings


@lru_cache
def _get_fernet() -> Fernet:
    # Deriva una clave válida de 32 bytes en formato urlsafe base64
    derived = hashlib.sha256(settings.encryption_secret.encode("utf-8")).digest()
    key = base64.urlsafe_b64encode(derived)
    return Fernet(key)


def normalize_phone(phone: str) -> str:
    # Deja solo los dígitos
    return "".join(c for c in phone if c.isdigit())


def hash_phone(phone: str) -> str:
    """Genera un hash determinista HMAC-SHA256 para indexación y búsqueda rápida."""
    clean = normalize_phone(phone)
    return hmac.new(settings.encryption_secret.encode("utf-8"), clean.encode("utf-8"), hashlib.sha256).hexdigest()


def encrypt_phone(phone: str) -> str:
    """Cifra el número de teléfono con Fernet (AES-128-CBC + HMAC)."""
    clean = normalize_phone(phone)
    return _get_fernet().encrypt(clean.encode("utf-8")).decode("utf-8")


def decrypt_phone(encrypted_phone: str) -> str:
    """Descifra el número de teléfono."""
    try:
        return _get_fernet().decrypt(encrypted_phone.encode("utf-8")).decode("utf-8")
    except Exception:
        return ""


def clean_first_name(raw_name: str | None) -> str:
    """Obtiene el primer nombre limpio a partir del perfil de WhatsApp."""
    if not raw_name:
        return "Amigo"
    cleaned = raw_name.strip()
    first = cleaned.split()[0]
    first = "".join(c for c in first if c.isalnum() or c in "áéíóúÁÉÍÓÚñÑ")
    return first.capitalize() if first else "Amigo"


def generate_user_code(phone: str, name: str) -> str:
    """Genera un identificador único visible y seguro (ej: JB-8F2A1C90)."""
    clean_p = normalize_phone(phone)
    seed = f"{clean_p}:{name.lower().strip()}:{settings.encryption_secret}"
    digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()[:8].upper()
    return f"JB-{digest}"
