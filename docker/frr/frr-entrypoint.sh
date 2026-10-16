#!/bin/bash
# NetPulse — entrypoint de los routers FRR del laboratorio.
# Arranca sshd (para NAPALM) y los daemons de FRR.
#
# Nota: no usamos `watchfrr` (docker-start) porque bajo emulación x86_64
# en Apple Silicon zebra puede terminar en "signal 11" al inicio. Un
# watchdog ligero reintenta arrancar cualquier daemon que esté caído.

# Servidor SSH — NAPALM se conecta por aquí (admin/admin)
/usr/sbin/sshd

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
