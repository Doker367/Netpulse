// Package config carga la red señuelo declarada en network.yaml.
//
// Es la misma topología que consume el motor nativo de Python, de modo
// que ambos data planes comparten la fuente de verdad (segmentos,
// dispositivos, servicios y personas).
package config

import (
	"fmt"
	"os"

	"gopkg.in/yaml.v3"
)

// Service es un servicio expuesto por un dispositivo señuelo.
type Service struct {
	Port   int    `yaml:"port"`
	Proto  string `yaml:"proto"`
	Engine string `yaml:"engine"`
	Banner string `yaml:"banner"`
}

// Persona es la identidad falsa del dispositivo.
type Persona struct {
	Vendor   string `yaml:"vendor"`
	OS       string `yaml:"os"`
	Hostname string `yaml:"hostname"`
	MAC      string `yaml:"mac"`
}

// Device es un dispositivo virtual de la red señuelo.
type Device struct {
	ID       string    `yaml:"id"`
	Role     string    `yaml:"role"`
	Segment  string    `yaml:"segment"`
	IP       string    `yaml:"ip"`
	Persona  Persona   `yaml:"persona"`
	Services []Service `yaml:"services"`
	Enabled  *bool     `yaml:"enabled"`
}

// IsEnabled indica si el dispositivo debe levantarse (por defecto sí).
func (d Device) IsEnabled() bool { return d.Enabled == nil || *d.Enabled }

// Segment es un segmento/VLAN de la red señuelo.
type Segment struct {
	ID      string `yaml:"id"`
	VLANID  int    `yaml:"vlan_id"`
	CIDR    string `yaml:"cidr"`
	Gateway string `yaml:"gateway"`
}

// Network es el documento raíz de network.yaml.
type Network struct {
	Segments []Segment `yaml:"segments"`
	Devices  []Device  `yaml:"devices"`
}

// Load lee y deserializa network.yaml.
func Load(path string) (*Network, error) {
	raw, err := os.ReadFile(path)
	if err != nil {
		return nil, fmt.Errorf("leyendo %s: %w", path, err)
	}
	var net Network
	if err := yaml.Unmarshal(raw, &net); err != nil {
		return nil, fmt.Errorf("parseando %s: %w", path, err)
	}
	return &net, nil
}
