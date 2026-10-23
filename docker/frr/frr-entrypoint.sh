#!/bin/bash
# NetPulse — entrypoint de los routers FRR del laboratorio.
# Arranca sshd (para NAPALM), lldpd (para el mapa de topología) y los daemons de FRR.
#
# Nota: no usamos `watchfrr` (docker-start) porque bajo emulación x86_64
# en Apple Silicon zebra puede terminar en "signal 11" al inicio. Un
# watchdog ligero reintenta arrancar cualquier daemon que esté caído.

# Servidor SSH — NAPALM se conecta por aquí (admin/admin)
/usr/sbin/sshd

# LLDP — NetPulse construye el mapa de topología con vecinos LLDP.
# FRR no habla LLDP: lldpd hace el anuncio L2 en todas las interfaces.
# El bloqueo del anuncio por la interfaz de gestión NO va aquí:
#   1) lldpd alpine 1.0.14: su -I (exclusión de interfaces) está rota
#      (cualquier patrón deja 0 interfaces),
#   2) iptables dentro del contenedor no tiene módulos del kernel en la VM
#      de Docker Desktop.
# La política de LLDP la maneja scripts/lab-net-lldp.sh a nivel del bridge
# de Docker (group_fwd_mask de cada bridge).
if command -v lldpd >/dev/null 2>&1; then
    pkill lldpd >/dev/null 2>&1 || true
    lldpd || true
fi

# El socket de control de lldpd (/run/lldp/lldpd.socket) es del grupo `lldpd`
# con modo 770: sin esto, el usuario SSH `admin` que usa NetPulse no puede
# leerlo y `lldpcli show neighbors` (fallback LLDP del mapa) sale vacío.
adduser admin lldpd >/dev/null 2>&1 || true

. /usr/lib/frr/frrcommon.sh
daemons="$(daemon_list || true)"

watchdog() {
    while true; do
        for d in $daemons; do
            pidfile="/run/frr/$d.pid"
            if [ -f "$pidfile" ] && kill -0 "$(cat "$pidfile")" 2>/dev/null; then
                continue
            fi
            daemon_start "$d" >/dev/null 2>&1 || true
        done
        sleep 5
    done
}

watchdog &

# Mantiene el contenedor en primer plano
exec tail -f /dev/null
