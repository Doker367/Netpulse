// Package ingest envía eventos normalizados a NetPulse.
//
// Contrato: POST /api/deception/ingest con cabecera X-Deception-Token.
// El envío es asíncrono (goroutine) para no frenar a los listeners.
package ingest

import (
	"bytes"
	"encoding/json"
	"log"
	"net/http"
	"time"
)

// Event es el evento normalizado que espera NetPulse.
type Event struct {
	DeviceID string         `json:"device_id"`
	SrcIP    string         `json:"src_ip"`
	SrcPort  int            `json:"src_port,omitempty"`
	Proto    string         `json:"proto"`
	Type     string         `json:"type"`
	Username string         `json:"username,omitempty"`
	Success  bool           `json:"success"`
	Detail   map[string]any `json:"detail,omitempty"`
}

// Client publica eventos en el endpoint de ingest de NetPulse.
type Client struct {
	URL   string
	Token string
	hc    *http.Client
}

// New crea un cliente de ingest con timeout corto.
func New(url, token string) *Client {
	return &Client{
		URL:   url,
		Token: token,
		hc:    &http.Client{Timeout: 5 * time.Second},
	}
}

// Emit envía un evento de forma asíncrona (best-effort).
func (c *Client) Emit(e Event) {
	go func() {
		body, err := json.Marshal(e)
		if err != nil {
			log.Printf("[ingest] marshal: %v", err)
			return
		}
		req, err := http.NewRequest(http.MethodPost, c.URL, bytes.NewReader(body))
		if err != nil {
			log.Printf("[ingest] request: %v", err)
			return
		}
		req.Header.Set("Content-Type", "application/json")
		if c.Token != "" {
			req.Header.Set("X-Deception-Token", c.Token)
		}
		resp, err := c.hc.Do(req)
		if err != nil {
			log.Printf("[ingest] envío a %s falló: %v", c.URL, err)
			return
		}
		defer resp.Body.Close()
		if resp.StatusCode >= 300 {
			log.Printf("[ingest] NetPulse respondió %d", resp.StatusCode)
		}
	}()
}
