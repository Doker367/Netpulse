# Seguridad

Secretos separados por producto; no se versionan. Listeners, polling, QEMU, bloqueos y descubrimiento requieren aprobación operacional. Dystopia debe separar API de control y red señuelo mediante interfaz/VLAN dedicada; Cowrie/OpenCanary solo en red aislada. VNC/serial y packet mode implican exposición elevada y permanecen desactivados por defecto.
