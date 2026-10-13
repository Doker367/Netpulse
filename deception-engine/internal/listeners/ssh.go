package listeners

import (
	"bufio"
	"crypto/rand"
	"crypto/rsa"
	"net"
	"strings"
	"sync"

	"golang.org/x/crypto/ssh"

	"netpulse/deception-engine/internal/personas"
)

var (
	hostKeyOnce sync.Once
	hostKey     ssh.Signer
	hostKeyErr  error
)

func getHostKey() (ssh.Signer, error) {
	hostKeyOnce.Do(func() {
		key, err := rsa.GenerateKey(rand.Reader, 2048)
		if err != nil {
			hostKeyErr = err
			return
		}
		hostKey, hostKeyErr = ssh.NewSignerFromKey(key)
	})
	return hostKey, hostKeyErr
}

// SSHListener es un servidor SSH señuelo: acepta cualquier credencial y
// captura los comandos ejecutados.
type SSHListener struct {
	base
	ln net.Listener
}

// Proto identifica el protocolo.
func (l *SSHListener) Proto() string { return "ssh" }

// Addr devuelve host:puerto.
func (l *SSHListener) Addr() string { return l.addr() }

// Start abre el socket y atiende conexiones.
func (l *SSHListener) Start() error {
	signer, err := getHostKey()
	if err != nil {
		return err
	}
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
			go l.handleConn(conn, signer)
		}
	}()
	return nil
}

// Stop cierra el socket.
func (l *SSHListener) Stop() error {
	if l.ln != nil {
		return l.ln.Close()
	}
	return nil
}

func (l *SSHListener) handleConn(nConn net.Conn, signer ssh.Signer) {
	defer nConn.Close()

	cfg := &ssh.ServerConfig{
		ServerVersion: "SSH-2.0-" + personas.Banner(l.role, l.banner),
	}
	cfg.AddHostKey(signer)
	cfg.PasswordCallback = func(c ssh.ConnMetadata, pass []byte) (*ssh.Permissions, error) {
		srcIP, srcPort := splitAddr(c.RemoteAddr().String())
		l.emit(srcIP, srcPort, "ssh", "login_success", c.User(), true,
			map[string]any{"service": "ssh", "password": string(pass)})
		return nil, nil
	}
	cfg.PublicKeyCallback = func(c ssh.ConnMetadata, _ ssh.PublicKey) (*ssh.Permissions, error) {
		srcIP, srcPort := splitAddr(c.RemoteAddr().String())
		l.emit(srcIP, srcPort, "ssh", "login_success", c.User(), true,
			map[string]any{"service": "ssh", "auth": "publickey"})
		return nil, nil
	}
	cfg.KeyboardInteractiveCallback = func(c ssh.ConnMetadata, _ ssh.KeyboardInteractiveChallenge) (*ssh.Permissions, error) {
		srcIP, srcPort := splitAddr(c.RemoteAddr().String())
		l.emit(srcIP, srcPort, "ssh", "login_success", c.User(), true,
			map[string]any{"service": "ssh", "auth": "keyboard-interactive"})
		return nil, nil
	}

	serverConn, chans, reqs, err := ssh.NewServerConn(nConn, cfg)
	if err != nil {
		return
	}
	defer serverConn.Close()
	go ssh.DiscardRequests(reqs)

	srcIP, srcPort := splitAddr(serverConn.RemoteAddr().String())
	username := serverConn.User()

	for newChan := range chans {
		if newChan.ChannelType() != "session" {
			_ = newChan.Reject(ssh.UnknownChannelType, "only session")
			continue
		}
		channel, requests, err := newChan.Accept()
		if err != nil {
			continue
		}
		go l.handleSession(channel, requests, srcIP, srcPort, username)
	}
}

func (l *SSHListener) handleSession(ch ssh.Channel, requests <-chan *ssh.Request,
	srcIP string, srcPort int, username string) {
	defer ch.Close()
	for req := range requests {
		switch req.Type {
		case "shell", "pty-req":
			_ = req.Reply(true, nil)
			if req.Type == "shell" {
				l.shell(ch, srcIP, srcPort, username)
			}
		case "exec":
			_ = req.Reply(true, nil)
			var payload struct{ Command string }
			if err := ssh.Unmarshal(req.Payload, &payload); err == nil {
				l.command(ch, srcIP, srcPort, username, payload.Command)
			}
			return
		default:
			_ = req.Reply(false, nil)
		}
	}
}

func (l *SSHListener) command(ch ssh.Channel, srcIP string, srcPort int, username, cmd string) {
	l.emit(srcIP, srcPort, "ssh", "command", username, false,
		map[string]any{"service": "ssh", "command": cmd})
	if resp := personas.Response(cmd); resp != "" {
		_, _ = ch.Write([]byte(resp + "\r\n"))
	}
}

func (l *SSHListener) shell(ch ssh.Channel, srcIP string, srcPort int, username string) {
	prompt := personas.Prompt(l.role)
	_, _ = ch.Write([]byte("\r\n" + prompt))
	reader := bufio.NewReader(ch)
	for {
		line, err := reader.ReadString('\n')
		if err != nil {
			return
		}
		cmd := strings.TrimSpace(line)
		if cmd == "" {
			_, _ = ch.Write([]byte(prompt))
			continue
		}
		l.emit(srcIP, srcPort, "ssh", "command", username, false,
			map[string]any{"service": "ssh", "command": cmd})
		if resp := personas.Response(cmd); resp != "" {
			_, _ = ch.Write([]byte(resp + "\r\n" + prompt))
		} else {
			_, _ = ch.Write([]byte(prompt))
		}
	}
}
