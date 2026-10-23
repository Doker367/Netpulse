# Rootfs del RouterOS del laboratorio (persistido fuera de git)

Este directorio debe contener el `rootfs/` extraído de la imagen
[`vaerh/routeros`](https://hub.docker.com/r/vaerh/routeros) (disco `.vdi` +
scripts del entrypoint) que `docker-compose.lab.yml` monta en `/routeros`
del servicio `router-mikrotik`. En git NO vive (50+ MB binarios, ver
`.gitignore`); si viene de un clone limpio, extráelo así:

```sh
mkdir -p docker/routeros/rootfs
docker create --name tmp-ros vaerh/routeros:latest
docker cp tmp-ros:/routeros/. docker/routeros/rootfs/
docker rm tmp-ros
```

- `rootfs/chr-7.22.vdi` — disco del RouterOS: la configuración hecha desde el
  panel/SSH (API 8728, identity…) sobrevive recreaciones. Borrarlo = factory
  reset.
- `rootfs/qemu-ifup` — ya incluye el `group_fwd_mask 0x4000` necesario en el
  bridge interno `qemubr1` para que el LLDP cruce hacia el segmento DMZ.
- La imagen solo crea **una** NIC qemu: el RouterOS se conecta únicamente a
  `dmz-net` (172.30.3.13 vía el udhcpd del contenedor).
- Services publicados al host: SSH 2225 (admin sin password; usar el cliente
  OpenSSH, paramiko no negocia el kex de ROS), API 8728, Winbox 8291.
