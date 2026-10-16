# Migración

Origen de referencia: `/Users/doker/Netpulse` en `c21d01461c874f3cd2ac2a35775847023722925d`; árbol limpio. Snapshot creado 2026-10-16, sin historial Git ni datos runtime/secretos.

## Secretos
Genere JWT, Fernet, usuarios, tokens y claves SSH distintos. Nunca copie secretos entre productos. Para NOC, copie inventario/usuarios/Fernet al servidor final por canal seguro y solo con autorización; no regenere la Fernet existente. Arranque poller/scheduler deshabilitados hasta confirmar que el NOC anterior está detenido para evitar doble sondeo.

## Corte seguro del NOC

1. Exporte inventario, usuarios, backups y la Fernet existente sin versionarlos.
2. Detenga scheduler/poller del NOC anterior y confirme que no quedan procesos.
3. Transfiera por canal cifrado, preserve permisos y no regenere Fernet.
4. Arranque el nuevo NOC con collectors deshabilitados.
5. Con autorización de ventana, valide en solo lectura los cinco equipos probados: inventario, acceso, SSH/Telnet/SNMP, recursos, comandos inocuos y modo dual; habilite polling de forma gradual.
6. Nunca mantenga dos NOC sondeando los mismos equipos.
