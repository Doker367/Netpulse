# ============================================================
# NetPulse — FRR con acceso SSH (necesario para NAPALM)
# ============================================================
# La imagen oficial frrouting/frr NO incluye servidor SSH, pero los
# drivers NAPALM (ios) se conectan por SSH. Esta capa añade openssh
# y arranca sshd antes de los daemons de FRR.
#
# Se construye una sola vez y la comparten los tres routers FRR.
# ============================================================
FROM frrouting/frr:latest

COPY admin-shell /usr/local/bin/admin-shell
COPY frr-entrypoint.sh /usr/local/bin/frr-entrypoint.sh

# NOTA llddpd alpine: su opción -I/exclusión de interfaces está rota en la 1.0.14
# (cualquier patrón deja 0 interfaces). Por eso NO usamos -I: lldpd anuncia en
# todas las interfaces y la política de anuncio en el segmento de gestión la
# maneja scripts/lab-net-lldp.sh (group_fwd_mask del bridge de Docker).
RUN apk add --no-cache openssh lldpd iputils \
    && ssh-keygen -A \
    && adduser -D -s /usr/local/bin/admin-shell admin \
    && echo 'admin:admin' | chpasswd \
    && echo 'root:frr' | chpasswd \
    && addgroup admin frr 2>/dev/null || true \
    && addgroup admin frrvty 2>/dev/null || true \
    && adduser admin lldpd 2>/dev/null || true \
    && echo '/usr/local/bin/admin-shell' >> /etc/shells \
    && chmod 755 /usr/local/bin/admin-shell \
    && sed -i 's/^#\?PermitRootLogin.*/PermitRootLogin yes/' /etc/ssh/sshd_config \
    && sed -i 's/^#\?PasswordAuthentication.*/PasswordAuthentication yes/' /etc/ssh/sshd_config
RUN chmod +x /usr/local/bin/frr-entrypoint.sh

CMD ["/usr/local/bin/frr-entrypoint.sh"]
