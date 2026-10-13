package listeners

import (
	"bufio"
	"net"
	"strings"

	"netpulse/deception-engine/internal/personas"
)

// TelnetListener simula un login con banner y una shell falsa.
type TelnetListener struct {
	base
	ln net.Listener
}

// Proto identifica el protocolo.
func (l *TelnetListener) Proto() string { return "telnet" }

// Addr devuelve host:puerto.
func (l *TelnetListener) Addr() string { return l.addr() }

// Start abre el socket y atiende conexiones.
func (l *TelnetListener) Start() error {
	ln, err := net.Listen("tcp", l.addr())
	if err != nil {
		return err
	}
	l.ln = ln
	go func() {
		for {
			conn, err := ln.Accept()
			if err != nil {
				return
			}
			go l.handleConn(conn)
		}
	}()
	return nil
}

// Stop cierra el socket.
func (l *TelnetListener) Stop() error {
	if l.ln != nil {
		return l.ln.Close()
	}
	return nil
}

func (l *TelnetListener) handleConn(conn net.Conn) {
	defer conn.Close()
	srcIP, srcPort := splitAddr(conn.RemoteAddr().String())
	l.emit(srcIP, srcPort, "telnet", "connect", "", false, nil)

	banner := personas.Banner(l.role, l.banner)
	_, _ = conn.Write([]byte("\r\n" + banner + "\r\n"))
	reader := bufio.NewReader(conn)

	_, _ = conn.Write([]byte("login: "))
	username, err := reader.ReadString('\n')
	if err != nil {
		return
	}
	username = strings.TrimSpace(username)
	l.emit(srcIP, srcPort, "telnet", "login_attempt", username, false,
		map[string]any{"service": "telnet"})

	_, _ = conn.Write([]byte("Password: "))
	password, err := reader.ReadString('\n')
	if err != nil {
		return
	}
	password = strings.TrimSpace(password)

	success := personas.ValidCredential(l.role, username, password)
	etype := "login_attempt"
	if success {
		etype = "login_success"
	}
	l.emit(srcIP, srcPort, "telnet", etype, username, success,
		map[string]any{"service": "telnet", "password": password})

	prompt := personas.Prompt(l.role)
	_, _ = conn.Write([]byte("\r\n" + prompt))
	for {
		line, err := reader.ReadString('\n')
		if err != nil {
			return
		}
		cmd := strings.TrimSpace(line)
		if cmd == "" {
			_, _ = conn.Write([]byte(prompt))
			continue
		}
		l.emit(srcIP, srcPort, "telnet", "command", username, false,
			map[string]any{"service": "telnet", "command": cmd})
		if resp := personas.Response(cmd); resp != "" {
			_, _ = conn.Write([]byte(resp + "\r\n" + prompt))
		} else {
			_, _ = conn.Write([]byte(prompt))
		}
	}
}
