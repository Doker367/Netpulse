package listeners

import (
	"net"
)

// SNMPListener recibe datagramas SNMP (UDP) y registra la consulta.
type SNMPListener struct {
	base
	conn *net.UDPConn
}

// Proto identifica el protocolo.
func (l *SNMPListener) Proto() string { return "snmp" }

// Addr devuelve host:puerto.
func (l *SNMPListener) Addr() string { return l.addr() }

// Start abre el socket UDP y atiende datagramas.
func (l *SNMPListener) Start() error {
	addr, err := net.ResolveUDPAddr("udp", l.addr())
	if err != nil {
		return err
	}
	conn, err := net.ListenUDP("udp", addr)
	if err != nil {
		return err
	}
	l.conn = conn
	go func() {
		buf := make([]byte, 4096)
		for {
			n, remote, err := conn.ReadFromUDP(buf)
			if err != nil {
				return
			}
			l.emit(remote.IP.String(), remote.Port, "snmp", "snmp_query", "", false,
				map[string]any{"service": "snmp", "bytes": n})
		}
	}()
	return nil
}

// Stop cierra el socket.
func (l *SNMPListener) Stop() error {
	if l.conn != nil {
		return l.conn.Close()
	}
	return nil
}
