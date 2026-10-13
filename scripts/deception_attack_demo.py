"""Demo: simula las acciones de un atacante contra la red señuelo.

Uso (con el demo dev de puertos altos):

    # 1) arrancar NetPulse con la red de desarrollo
    NETPULSE_DECEPTION_ENABLED=true \
    NETPULSE_DECEPTION_BIND_HOST=127.0.0.1 \
    NETPULSE_DECEPTION_NETWORK_FILE=config/deception/network.dev.yaml \
    NETPULSE_METRICS_ENABLED=false NETPULSE_SYSLOG_ENABLED=false \
    .venv/bin/uvicorn app.main:app --port 8082

    # 2) lanzar el ataque simulado
    .venv/bin/python scripts/deception_attack_demo.py

    # 3) mirar los eventos en el dashboard (sección DECEPTION) o por API
"""

import socket
import time
import urllib.request
import urllib.parse

import paramiko

HOST = "127.0.0.1"


def ssh_attack(port, user, pwd, commands, label):
    print(f"[+] SSH {label} ({HOST}:{port}) como {user}/{pwd}")
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(HOST, port=port, username=user, password=pwd,
              allow_agent=False, look_for_keys=False, timeout=5)
    ch = c.invoke_shell()
    time.sleep(0.3)
    for cmd in commands:
        ch.send(cmd + "\n")
        time.sleep(0.3)
        try:
            ch.recv(4096)
        except Exception:
            pass
    ch.close()
    c.close()


def telnet_attack(port, user, pwd, commands):
    print(f"[+] TELNET cam-dev-01 ({HOST}:{port}) como {user}/{pwd}")
    s = socket.create_connection((HOST, port), timeout=5)
    s.settimeout(2)

    def recv():
        try:
            return s.recv(4096)
        except socket.timeout:
            return b""

    recv()
    s.sendall(user.encode() + b"\r\n")
    time.sleep(0.2)
    recv()
    s.sendall(pwd.encode() + b"\r\n")
    time.sleep(0.2)
    recv()
    for cmd in commands:
        s.sendall(cmd.encode() + b"\r\n")
        time.sleep(0.2)
        recv()
    s.close()


def http_get(url):
    print(f"[+] HTTP GET {url}")
    try:
        urllib.request.urlopen(url, timeout=5).read()
    except Exception as exc:
        print("    (respuesta)", exc)


def http_login(url, user, pwd):
    print(f"[+] HTTP POST login {url} {user}/{pwd}")
    data = urllib.parse.urlencode({"username": user, "password": pwd}).encode()
    req = urllib.request.Request(url, data=data)
    try:
        urllib.request.urlopen(req, timeout=5).read()
    except Exception:
        pass


def rtsp_probe(port):
    print(f"[+] RTSP OPTIONS {HOST}:{port}")
    s = socket.create_connection((HOST, port), timeout=5)
    s.sendall(b"OPTIONS rtsp://%s RTSP/1.0\r\nCSeq: 1\r\n\r\n" % HOST.encode())
    s.settimeout(2)
    try:
        print("    <-", s.recv(256).decode("latin-1", "replace").splitlines()[0])
    except Exception:
        pass
    s.close()


def snmp_probe(port):
    print(f"[+] SNMP query (UDP) {HOST}:{port} comunidad 'public'")
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.sendto(b"\x30\x26\x02\x01\x00\x04\x06public\xa0\x19", (HOST, port))
    s.close()


if __name__ == "__main__":
    ssh_attack(2202, "root", "toor",
               ["whoami", "id", "uname -a", "cat /etc/passwd"], "srv-dev-01")
    ssh_attack(2203, "admin", "Fortinet", ["show system status"], "fw-dev-01")
    telnet_attack(2301, "admin", "1234", ["ls", "cat /etc/passwd", "reboot"])
    http_get("http://127.0.0.1:8002/")
    http_login("http://127.0.0.1:8002/login", "admin", "12345")
    rtsp_probe(5540)
    snmp_probe(1161)
    print("[+] Ataque simulado terminado")
