from datetime import datetime
from zoneinfo import ZoneInfo

try:
    CHILE_TZ = ZoneInfo("America/Santiago")
except Exception:
    CHILE_TZ = None


def get_now() -> datetime:
    """Retorna la fecha y hora actual en la zona horaria de Chile (America/Santiago)."""
    return datetime.now(CHILE_TZ) if CHILE_TZ else datetime.now()


def get_current_month() -> str:
    """Retorna el mes actual en formato 'YYYY-MM' considerando la hora de Chile."""
    return get_now().strftime("%Y-%m")
