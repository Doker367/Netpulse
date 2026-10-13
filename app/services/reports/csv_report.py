"""NetPulse — Reportes CSV.

Genera reportes en formato CSV a partir del inventario y de los health
scores. Se usan tanto desde el router de integraciones como desde el
scheduler (reportes periódicos).
"""

import csv
import io
from typing import Iterable, Sequence

from app.services import inventory_svc

INVENTORY_HEADERS = [
    "id", "hostname", "port", "driver", "protocol",
    "type", "group", "tags", "description",
]


def generate_inventory_csv() -> str:
    """Reporte CSV con el inventario completo de dispositivos.

    Returns:
        Contenido CSV como string (incluye fila de cabecera).
    """
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=INVENTORY_HEADERS,
                            extrasaction="ignore", lineterminator="\n")
    writer.writeheader()
    for device in inventory_svc.list_devices():
        row = dict(device)
        tags = row.get("tags") or []
        row["tags"] = "|".join(str(t) for t in tags)
        writer.writerow(row)
    return buffer.getvalue()


def _union_headers(rows: Sequence[dict]) -> list[str]:
    """Cabeceras a partir de la unión de claves, preservando el orden."""
    headers: list[str] = []
    for row in rows:
        for key in row:
            if key not in headers:
                headers.append(key)
    return headers


def generate_devices_csv(rows: Iterable[dict]) -> str:
    """Reporte CSV genérico a partir de filas de dispositivos/health.

    Args:
        rows: Lista de dicts (ej. salida de ``health_svc.health_all()``).

    Returns:
        Contenido CSV como string.
    """
    rows = list(rows)
    buffer = io.StringIO()
    headers = _union_headers(rows)
    if not headers:
        return ""
    writer = csv.DictWriter(buffer, fieldnames=headers,
                            extrasaction="ignore", lineterminator="\n")
    writer.writeheader()
    for row in rows:
        writer.writerow({h: row.get(h, "") for h in headers})
    return buffer.getvalue()
