// Package listeners agrupa los servidores señuelo del data plane en Go.
//
// Cada listener atiende un protocolo, reporta las interacciones del
// atacante a NetPulse vía el cliente de ingest y responde con una
// persona falsa (banner/CLI/panel).
package listeners

import (
	"log"
	"net"
	"strconv"
	"strings"
	"sync"

	"netpulse/deception-engine/internal/config"
	"netpulse/deception-engine/internal/ingest"
)

// Listener es un servidor señuelo.
type Listener interface {
	Start() error
	Stop() error
	Proto() string
	Addr() string
}

// base contiene lo común a todos los listeners.
type base struct {
	deviceID string
	role     string
	host     string
	port     int
	banner   string
	cli      *ingest.Client
}

func (b *base) emit(srcIP string, srcPort int, proto, etype, username string, success bool, detail map[string]any) {
	b.cli.Emit(ingest.Event{
		DeviceID: b.deviceID, SrcIP: srcIP, SrcPort: srcPort, Proto: proto,
		Type: etype, Username: username, Success: success, Detail: detail,
	})
}

func (b *base) addr() string {
	return net.JoinHostPort(b.host, strconv.Itoa(b.port))
}

func splitAddr(remote string) (string, int) {
	host, portStr, err := net.SplitHostPort(remote)
	if err != nil {
		return remote, 0
	}
	port, _ := strconv.Atoi(portStr)
	return host, port
}

// New construye el listener adecuado para un servicio (nil si no soportado).
func New(dev config.Device, svc config.Service, bindHost string, cli *ingest.Client) Listener {
	host := bindHost
	if host == "" {
		host = dev.IP
	}
	b := base{
		deviceID: dev.ID,
		role:     dev.Role,
		host:     host,
		port:     svc.Port,
		banner:   svc.Banner,
		cli:      cli,
	}
	switch strings.ToLower(svc.Proto) {
	case "ssh":
		return &SSHListener{base: b}
	case "http", "https":
		return &HTTPListener{base: b, protoName: strings.ToLower(svc.Proto)}
	case "telnet":
		return &TelnetListener{base: b}
	case "snmp":
		return &SNMPListener{base: b}
	case "rtsp", "smb":
		return &BannerListener{base: b, proto: strings.ToLower(svc.Proto)}
	default:
		return nil
	}
}

// Manager supervisa el ciclo de vida de todos los listeners.
type Manager struct {
	mu    sync.Mutex
	items []Listener
}

// NewManager crea un manager vacío.
func NewManager() *Manager { return &Manager{} }

// StartAll levanta un listener por servicio de cada dispositivo habilitado.
func (m *Manager) StartAll(netCfg *config.Network, bindHost string, cli *ingest.Client) (started, failed int) {
	for _, dev := range netCfg.Devices {
		if !dev.IsEnabled() {
			continue
		}
		for _, svc := range dev.Services {
			l := New(dev, svc, bindHost, cli)
			if l == nil {
				continue
			}
			if err := l.Start(); err != nil {
				log.Printf("[engine] no se pudo levantar %s %s: %v", dev.ID, l.Addr(), err)
				failed++
				continue
			}
			log.Printf("[engine] %s escuchando en %s (%s/%s)", l.Proto(), l.Addr(), dev.ID, dev.Role)
			m.mu.Lock()
			m.items = append(m.items, l)
			m.mu.Unlock()
			started++
		}
	}
	return started, failed
}

// StopAll detiene todos los listeners.
func (m *Manager) StopAll() {
	m.mu.Lock()
	defer m.mu.Unlock()
	for _, l := range m.items {
		if err := l.Stop(); err != nil {
			log.Printf("[engine] error deteniendo %s %s: %v", l.Proto(), l.Addr(), err)
		}
	}
	m.items = nil
}

// Count devuelve la cantidad de listeners activos.
func (m *Manager) Count() int {
	m.mu.Lock()
	defer m.mu.Unlock()
	return len(m.items)
}
