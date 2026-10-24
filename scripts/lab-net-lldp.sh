#!/bin/sh
# NetPulse — política LLDP para los bridges de Docker del laboratorio.
#
# El kernel de Linux NO reenvía frames LLDP (multicast link-local) por un
# bridge a menos que `group_fwd_mask` lo permita (bit 14 = 0x4000). Docker
# Desktop además no tiene los módulos de iptables/ebtables en su VM, así que
# la única forma de controlar el reenvío LLDP es ese sysfs (dentro de la VM).
#
# Resultado en el mapa de topología:
#   - lab-net (gestión del NOC): LLDP FILTRADO → el mapa solo dibuja enlaces
#     de los segmentos del lab, no la intranet de gestión.
#   - core-net / access-net / dmz-net: LLDP permitido → enlaces reales
#     cisco↔juniper (core), cisco↔switch (access), juniper↔mikrotik (dmz);
#     el resto de los pares quedan sin enlace y se ven aislados.
#
# Uso:  ./scripts/lab-net-lldp.sh            (aplicar la política)
#       ./scripts/lab-net-lldp.sh --all-on   (permitir en TODOS, para debug)
#       ./scripts/lab-net-lldp.sh --all-off  (prohibir en TODOS)
#
# Además de las máscaras LLDP, deja "reparado" el entorno del lab:
#   * qemubr1 dentro del contenedor RouterOS también filtra LLDP sin su
#     group_fwd_mask (se aplica en su qemu-ifup, pero un recreate lo borra
#     hasta que ROS engancha su tap; esto lo repone).
#   * Docker Desktop añade reglas `DROP` en nat/PREROUTING anti-spoofing
#     por cada puerto publicado (¡dropean ICMP/UDP entre redes del lab!);
#     arriba de todo se inserta un ACCEPT para 172.30.0.0/16 y filter/FORWARD.

set -eu

LAB_PREFIX="netpulse-noc_"
MGMT_NET="${NETPULSE_LAB_MGMT_NET:-${LAB_PREFIX}lab-net}"
MODE="${1:-default}"
LAB_CIDR="172.30.0.0/16"

# Preparar fuera de la VM: "br-<id> <mask>" por cada red del lab.
args=""
for net in $(docker network ls --format '{{.Name}}'); do
    case "$net" in "$LAB_PREFIX"*) ;;
        *) continue ;;
    esac
    id="$(docker network inspect "$net" --format '{{.Id}}' | cut -c1-12)"
    case "$MODE" in
        --all-on) m=16384 ;;
        --all-off) m=0 ;;
        *) if [ "$net" = "$MGMT_NET" ]; then m=0; else m=16384; fi ;;
    esac
    args="$args $id:$m"
done

[ -n "$args" ] || { echo "⚠ sin redes ${LAB_PREFIX}* (¿el lab está levantado?)"; exit 0; }

# Aplicar dentro de la VM de Docker (el sysfs no es visible desde macOS).
docker run --rm --privileged -v /sys:/sys alpine sh -c '
for a in $@; do
  br="br-${a%%:*}"; m="${a##*:}"
  sys="/sys/class/net/$br/bridge/group_fwd_mask"
  if [ ! -e "$sys" ]; then
    echo "⚠ $br no existe en la VM (¿red removida/recreada?)"
    continue
  fi
  cur=$(cat "$sys")
  printf "%s\n" "$m" > "$sys"
  echo "$br: $cur -> $(cat "$sys")"
done' _ $args

# qemubr1 interno del contenedor RouterOS (su qemu-ifup ya lo aplica, pero
# esto repone la máscara si el tap se enganchó antes de la ola de recreación).
docker exec netpulse-mikrotik sh -c '
  echo 0x4000 > /sys/devices/virtual/net/qemubr1/bridge/group_fwd_mask 2>/dev/null && \
  echo "qemubr1 (ROS): group_fwd_mask → 0x4000" || echo "qemubr1 (ROS): no listo (¿arrancando?)"' \
  2>/dev/null || echo "qemubr1 (ROS): contenedor no disponible"

# Docker Desktop inserta DROP anti-spoofing (por puerto publicado) en
# nat/PREROUTING y DROP entre bridges en filter/FORWARD; levantamos un ACCEPT
# para el CIDR del lab por encima de todo. Idempotente.
docker run --rm --privileged --net=host -v /sys:/sys alpine sh -c '
apk add --no-cache iptables >/dev/null 2>&1 || true
if command -v iptables >/dev/null 2>&1; then
  iptables -t nat -C PREROUTING -s '"$LAB_CIDR"' -d '"$LAB_CIDR"' -j ACCEPT 2>/dev/null || \
    iptables -t nat -I PREROUTING 1 -s '"$LAB_CIDR"' -d '"$LAB_CIDR"' -j ACCEPT
  iptables -t nat -C PREROUTING -s '"$LAB_CIDR"' -d '"$LAB_CIDR"' -j ACCEPT && echo "nat/PREROUTING: ACCEPT '"$LAB_CIDR"' ok"
  iptables -C FORWARD -s '"$LAB_CIDR"' -d '"$LAB_CIDR"' -j ACCEPT 2>/dev/null || \
    iptables -I FORWARD 1 -s '"$LAB_CIDR"' -d '"$LAB_CIDR"' -j ACCEPT
  echo "filter/FORWARD: ACCEPT '"$LAB_CIDR"' ok"
else
  echo "⚠ sin iptables en la VM (¿alpine sin apk? traceroute inter-red no funcionará)"
fi'
