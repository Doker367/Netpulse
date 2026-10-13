#!/usr/bin/env bash
# ============================================================
# NetPulse — VLANs señuelo reales a nivel de enlace (802.1Q)
# ============================================================
# Crea subinterfaces VLAN etiquetadas sobre una interfaz base y las
# asocia a un bridge, para que los segmentos declarados en
# config/deception/network.yaml existan de verdad en el kernel (no solo
# como metadatos). Requiere Linux con privilegios (root/cap NET_ADMIN).
#
# Uso:
#   sudo ./scripts/deception_vlans.sh up   [iface] [bridge]
#   sudo ./scripts/deception_vlans.sh down [iface] [bridge]
#   (default iface=eth0, bridge=br-deception)
# ============================================================
set -euo pipefail

ACTION="${1:-up}"
BASE="${2:-eth0}"
BRIDGE="${3:-br-deception}"

# Segmento → (VLAN, CIDR gateway) — debe reflejar network.yaml.
VLANS=(10 20 30 40)

up() {
  if ! ip link show "$BRIDGE" >/dev/null 2>&1; then
    ip link add name "$BRIDGE" type bridge
    ip link set "$BRIDGE" up
    echo "bridge $BRIDGE creado"
  fi
  for vid in "${VLANS[@]}"; do
    dev="${BASE}.${vid}"
    if ! ip link show "$dev" >/dev/null 2>&1; then
      ip link add link "$BASE" name "$dev" type vlan id "$vid"
      ip link set "$dev" master "$BRIDGE"
      ip link set "$dev" up
      echo "subinterfaz $dev (VLAN $vid) creada y en $BRIDGE"
    else
      echo "subinterfaz $dev ya existe"
    fi
  done
  echo "VLANs señuelo activas sobre $BASE → $BRIDGE"
}

down() {
  for vid in "${VLANS[@]}"; do
    dev="${BASE}.${vid}"
    if ip link show "$dev" >/dev/null 2>&1; then
      ip link set "$dev" down || true
      ip link delete "$dev" || true
      echo "subinterfaz $dev eliminada"
    fi
  done
  if ip link show "$BRIDGE" >/dev/null 2>&1; then
    ip link set "$BRIDGE" down || true
    ip link delete "$BRIDGE" type bridge || true
    echo "bridge $BRIDGE eliminado"
  fi
}

case "$ACTION" in
  up) up ;;
  down) down ;;
  *) echo "uso: $0 {up|down} [iface] [bridge]"; exit 2 ;;
esac
