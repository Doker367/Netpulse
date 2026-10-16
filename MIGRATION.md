# Migración

Origen de referencia: `/Users/doker/Netpulse` en `c21d01461c874f3cd2ac2a35775847023722925d`; árbol limpio. Snapshot creado 2026-10-16, sin historial Git ni datos runtime/secretos.

## Secretos
Genere JWT, Fernet, usuarios, tokens y claves SSH distintos. Nunca copie secretos entre productos. Para NOC, copie inventario/usuarios/Fernet al servidor final por canal seguro y solo con autorización; no regenere la Fernet existente. Arranque poller/scheduler deshabilitados hasta confirmar que el NOC anterior está detenido para evitar doble sondeo.
