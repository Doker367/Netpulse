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

RUN apk add --no-cache openssh \
    && ssh-keygen -A \
    && adduser -D -s /usr/bin/vtysh admin \
    && echo 'admin:admin' | chpasswd \
    && echo 'root:frr' | chpasswd \
    && addgroup admin frr 2>/dev/null || true \
    && addgroup admin frrvty 2>/dev/null || true \
    && echo '/usr/bin/vtysh' >> /etc/shells \
    && sed -i 's/^#\?PermitRootLogin.*/PermitRootLogin yes/' /etc/ssh/sshd_config \
    && sed -i 's/^#\?PasswordAuthentication.*/PasswordAuthentication yes/' /etc/ssh/sshd_config

COPY frr-entrypoint.sh /usr/local/bin/frr-entrypoint.sh
RUN chmod +x /usr/local/bin/frr-entrypoint.sh

CMD ["/usr/local/bin/frr-entrypoint.sh"]
