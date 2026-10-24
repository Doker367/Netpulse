"""Utilidades de tiempo del colector: ISO-8601 UTC con microsegundos."""

from datetime import datetime, timezone


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso_now() -> str:
    return utcnow().isoformat(timespec="microseconds")


def iso_utc(value: datetime) -> str:
    """Normaliza un datetime con zona a UTC con microsegundos.

    Lanza ``ValueError`` si es naive: el contrato exige offset explícito.
    """
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp sin zona horaria")
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds")


def parse_iso(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("timestamp sin zona horaria")
    return parsed.astimezone(timezone.utc)


def age_seconds(value: str, now: datetime | None = None) -> float:
    current = now or utcnow()
    return max(0.0, (current - parse_iso(value)).total_seconds())
