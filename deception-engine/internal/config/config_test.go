package config

import (
	"os"
	"path/filepath"
	"testing"
)

func TestLoadNetwork(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "network.yaml")
	content := `segments:
  - id: vlan-x
    cidr: 10.99.0.0/24
devices:
  - id: cam-01
    role: camera
    segment: vlan-x
    ip: 10.99.0.11
    services:
      - { port: 80, proto: http, banner: "Hikvision-Webs" }
      - { port: 554, proto: rtsp }
  - id: off-01
    role: pc
    segment: vlan-x
    ip: 10.99.0.12
    enabled: false
    services: []
`
	if err := os.WriteFile(path, []byte(content), 0o600); err != nil {
		t.Fatal(err)
	}

	net, err := Load(path)
	if err != nil {
		t.Fatalf("Load: %v", err)
	}
	if len(net.Devices) != 2 {
		t.Fatalf("esperaba 2 dispositivos, obtuve %d", len(net.Devices))
	}
	if net.Devices[0].Services[0].Port != 80 || net.Devices[0].Services[0].Proto != "http" {
		t.Fatalf("servicio mal parseado: %+v", net.Devices[0].Services[0])
	}
	if len(net.Devices[0].Services) != 2 {
		t.Fatalf("esperaba 2 servicios, obtuve %d", len(net.Devices[0].Services))
	}
	if net.Devices[1].IsEnabled() {
		t.Fatalf("off-01 debería estar deshabilitado")
	}
}

func TestLoadMissingFile(t *testing.T) {
	if _, err := Load("/no/existe/network.yaml"); err == nil {
		t.Fatalf("esperaba error por archivo inexistente")
	}
}
