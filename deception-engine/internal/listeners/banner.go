package listeners

import (
	"io"
	"net"
	"strings"
	"time"
)

var rtspResponse = []byte("RTSP/1.0 200 OK\r\n" +
	"CSeq: 1\r\n" +
	"Server: DSS/6.0\r\n" +
	"Public: OPTIONS, DESCRIBE, SETUP, PLAY, TEARDOWN\r\n\r\n")

// BannerListener registra interacciones de protocolos "banner" (RTSP/SMB)
// y responde RTSP OPTIONS con un 200 OK creíble.
type BannerListener struct {
	base
	proto string
	ln    net.Listener
}

// Proto identifica el protocolo.
func (l *BannerListener) Proto() string { return l.proto }

// Addr devuelve host:puerto.
func (l *BannerListener) Addr() string { return l.addr() }

// Start abre el socket y atiende conexiones.
func (l *BannerListener) Start() error {
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
func (l *BannerListener) Stop() error {
	if l.ln != nil {
		return l.ln.Close()
	}
	return nil
}

func (l *BannerListener) handleConn(conn net.Conn) {
	defer conn.Close()
	_ = conn.SetReadDeadline(time.Now().Add(5 * time.Second))
	buf := make([]byte, 4096)
	n, err := conn.Read(buf)
	if err != nil && err != io.EOF {
		return
	}
	srcIP, srcPort := splitAddr(conn.RemoteAddr().String())
	preview := strings.TrimSpace(string(buf[:n]))
	if len(preview) > 200 {
		preview = preview[:200]
	}

	etype := "other"
	if l.proto == "rtsp" {
		etype = "rtsp_request"
	}
	l.emit(srcIP, srcPort, l.proto, etype, "", false,
		map[string]any{"service": l.proto, "request": preview})

	if l.proto == "rtsp" && strings.HasPrefix(strings.ToUpper(preview), "OPTIONS") {
		_, _ = conn.Write(rtspResponse)
	}
}
