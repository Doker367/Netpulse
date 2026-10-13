"""NetPulse — Reportes PDF (reportlab).

Genera PDFs del inventario, health scores y disponibilidad. Cada función
devuelve un dict con la ruta del archivo generado, que el router de
integraciones entrega como descarga.
"""

import logging
from datetime import datetime, timezone

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import (
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from app.core.settings import REPORT_DIR

logger = logging.getLogger(__name__)

_STYLES = getSampleStyleSheet()


def _timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")


def _build_pdf(filename: str, title: str, headers: list[str], rows: list[list]) -> dict:
    """Construye un PDF tabular simple y devuelve su ruta."""
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    path = REPORT_DIR / filename

    doc = SimpleDocTemplate(
        str(path), pagesize=A4,
        leftMargin=1.5 * cm, rightMargin=1.5 * cm,
        topMargin=1.5 * cm, bottomMargin=1.5 * cm,
    )
    elements = [
        Paragraph(title, _STYLES["Title"]),
        Paragraph(
            f"Generado: {datetime.now(timezone.utc).isoformat()}",
            _STYLES["Normal"],
        ),
        Spacer(1, 0.4 * cm),
    ]

    if rows:
        data = [headers] + [[str(c) for c in row] for row in rows]
        table = Table(data, repeatRows=1)
        table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1f2937")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTSIZE", (0, 0), (-1, -1), 8),
            ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1),
             [colors.white, colors.HexColor("#f3f4f6")]),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ]))
        elements.append(table)
    else:
        elements.append(Paragraph("Sin datos para mostrar.", _STYLES["Normal"]))

    doc.build(elements)
    logger.info("[reports] PDF generado: %s", path)
    return {"path": str(path), "filename": filename}


def generate_inventory_pdf() -> dict:
    """PDF con el inventario completo de dispositivos."""
    from app.services import inventory_svc

    headers = ["ID", "Host", "Puerto", "Driver", "Tipo", "Grupo", "Descripción"]
    rows = [
        [d["id"], d.get("hostname", ""), d.get("port", ""), d.get("driver", ""),
         d.get("type", ""), d.get("group") or "", d.get("description", "")]
        for d in inventory_svc.list_devices()
    ]
    return _build_pdf(f"inventory_{_timestamp()}.pdf", "NetPulse — Inventario", headers, rows)


def generate_health_pdf() -> dict:
    """PDF con los health scores de todos los dispositivos."""
    from app.services.ops import health_svc

    headers = ["Dispositivo", "Score", "CPU", "Memoria %", "Disco %", "Estado"]
    rows = [
        [r.get("device_id", ""), r.get("score", ""), r.get("cpu", ""),
         r.get("memory_pct", ""), r.get("disk_pct", ""), r.get("status", "")]
        for r in health_svc.health_all()
    ]
    return _build_pdf(f"health_{_timestamp()}.pdf", "NetPulse — Health Scores", headers, rows)


def generate_availability_pdf(device_id: str = "") -> dict:
    """PDF de disponibilidad a partir del historial de métricas."""
    from app.services import metrics_history_svc

    headers = ["Timestamp", "CPU %", "Memoria %", "Disco %", "Estado"]
    rows: list[list] = []

    if device_id:
        for sample in metrics_history_svc.device_series(device_id, hours=24):
            ts = datetime.fromtimestamp(sample.get("ts", 0), tz=timezone.utc).isoformat()
            rows.append([
                ts, sample.get("cpu", ""), sample.get("memory_percent", ""),
                sample.get("disk_percent", ""), sample.get("status", ""),
            ])
        title = f"NetPulse — Disponibilidad de {device_id}"
    else:
        for sample in metrics_history_svc.host_series(hours=24):
            ts = datetime.fromtimestamp(sample.get("ts", 0), tz=timezone.utc).isoformat()
            rows.append([
                ts, sample.get("cpu", ""), sample.get("memory_percent", ""),
                sample.get("disk_percent", ""), "up",
            ])
        title = "NetPulse — Disponibilidad del host"

    return _build_pdf(f"availability_{_timestamp()}.pdf", title, headers, rows)
