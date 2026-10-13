package listeners

import (
	"fmt"
	"io"
	"net"
	"net/http"
	"strings"

	"netpulse/deception-engine/internal/personas"
)

// HTTPListener sirve un panel de administración falso y captura las
// credenciales enviadas por formulario.
type HTTPListener struct {
	base
	protoName string
	ln        net.Listener
	srv       *http.Server
}

// Proto identifica el protocolo configurado (http/https).
func (l *HTTPListener) Proto() string { return l.protoName }

// Addr devuelve host:puerto.
func (l *HTTPListener) Addr() string { return l.addr() }

// Start levanta el servidor HTTP.
func (l *HTTPListener) Start() error {
	ln, err := net.Listen("tcp", l.addr())
	if err != nil {
		return err
	}
	l.ln = ln
	mux := http.NewServeMux()
	mux.HandleFunc("/", l.handle)
	l.srv = &http.Server{Handler: mux}
	go func() { _ = l.srv.Serve(ln) }()
	return nil
}

// Stop cierra el servidor.
func (l *HTTPListener) Stop() error {
	if l.srv != nil {
		return l.srv.Close()
	}
	return nil
}

func (l *HTTPListener) handle(w http.ResponseWriter, r *http.Request) {
	srcIP, srcPort := splitAddr(r.RemoteAddr)

	l.emit(srcIP, srcPort, l.protoName, "http_request", "", false, map[string]any{
		"service":    l.protoName,
		"method":     r.Method,
		"path":       r.URL.Path,
		"user_agent": r.UserAgent(),
	})

	if r.Method == http.MethodPost {
		_ = r.ParseForm()
		username := firstNonEmpty(r.FormValue("username"), r.FormValue("user"))
		password := firstNonEmpty(r.FormValue("password"), r.FormValue("pass"))
		l.emit(srcIP, srcPort, l.protoName, "login_attempt", username, false, map[string]any{
			"service": l.protoName, "path": r.URL.Path, "password": password,
		})
		http.Redirect(w, r, "/", http.StatusFound)
		return
	}

	vendor := personas.Banner(l.role, l.banner)
	w.Header().Set("Server", serverHeader(l.role))
	w.Header().Set("Content-Type", "text/html; charset=utf-8")
	_, _ = io.WriteString(w, fmt.Sprintf(
		`<!DOCTYPE html><html><head><title>%s - Login</title></head><body>`+
			`<h2>%s Administration</h2>`+
			`<form method="post" action="/login">`+
			`Usuario: <input name="username"><br>`+
			`Contraseña: <input name="password" type="password"><br>`+
			`<button type="submit">Entrar</button></form></body></html>`,
		vendor, vendor))
}

func firstNonEmpty(values ...string) string {
	for _, v := range values {
		if strings.TrimSpace(v) != "" {
			return v
		}
	}
	return ""
}

// serverHeader devuelve una cabecera Server creíble por rol.
func serverHeader(role string) string {
	switch role {
	case "camera":
		return "webs"
	case "switch":
		return "Cisco-IOS/15.2(4)E"
	case "firewall":
		return "FortiGate"
	case "router":
		return "RouterOS 7.11 httpd"
	case "pc":
		return "Microsoft-IIS/10.0"
	default:
		return "Apache/2.4.41 (Ubuntu)"
	}
}
