# NetPulse — Fase 2: Consola Web y Centralizador (NOC)

## 1. Consola Web (WebSSH)
- Endpoint WebSocket: `GET /ws/terminal/{device_id}?token=<JWT>` (`app/routers/terminal.py`).
- Abre una shell SSH interactiva (paramiko + PTY) contra el dispositivo y la
  puentea al navegador. Redimensionado de PTY soportado (`__resize:cols,rows`).
- Frontend: vista **Console** con xterm.js (vendored en `app/static/vendor/`):
  historial con ↑/↓, Ctrl+C, Tab, paginadores, etc. (los maneja el equipo).
- Solo SSH directo; dispositivos solo-SNMP no lo soportan.

## 2. Diff visual de configuraciones
- `GET /api/config/{device_id}/backups/diff?a=<file>&b=<file>` → filas
  alineadas (`equal/add/del/change`) + estadísticas.
- UI en vista **Comparar**: elegir dispositivo y dos backups → vista lado a
  lado con líneas añadidas (verde) y eliminadas (rojo).

## 3. Backup offsite (S3/MinIO o SFTP)
- Servicio: `app/services/backup_offsite_svc.py`.
- Endpoints: `GET /api/config/offsite/status`, `POST /api/config/offsite/export`.
- Config en `.env` (`NETPULSE_OFFSITE_*`); botón **☁ Offsite** en Backups.
- SFTP con clave privada o contraseña; S3 con `endpoint_url` (MinIO) o AWS.

## 4. Firewalls (Fortinet FortiOS)
- Servicio: `app/services/fortios_svc.py`; router `app/routers/firewall.py`.
- Endpoints (rol viewer): `GET /api/firewall/{device_id}/status|interfaces|policies|sessions|config`.
- Auth por API token (`Authorization: Bearer`) o login usuario/clave.
- El dispositivo del inventario debe apuntar al FortiGate (driver `fortios`,
  `api_port` opcional; por defecto 443) con el token en las credenciales.

## Variables nuevas
Ver `.env.example` → secciones *Backup offsite*; FortiOS:
`NETPULSE_FORTIOS_VERIFY` (por defecto false, certificados self-signed) y
`NETPULSE_FORTIOS_TIMEOUT`.
