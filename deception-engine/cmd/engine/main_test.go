package main

import (
	"testing"

	"netpulse/deception-engine/internal/config"
)

func TestDecoyMACUsesPersonaMAC(t *testing.T) {
	mac := decoyMAC(config.Persona{Vendor: "cisco", MAC: "00:1c:42:10:10:21"}, "10.10.0.21")
	if mac != "00:1c:42:10:10:21" {
		t.Fatalf("se esperaba la MAC de la persona, got %s", mac)
	}
}

func TestDecoyMACOUIForVendor(t *testing.T) {
	mac := decoyMAC(config.Persona{Vendor: "hikvision"}, "10.30.0.11")
	if mac != "bc:ba:e1:1e:00:0b" {
		t.Fatalf("se esperaba OUI Hikvision, got %s", mac)
	}
	mac = decoyMAC(config.Persona{Vendor: "mikrotik"}, "10.40.0.2")
	if mac != "74:4d:28:28:00:02" {
		t.Fatalf("se esperaba OUI MikroTik, got %s", mac)
	}
}

func TestBuildDecoyMACsSkipsDisabled(t *testing.T) {
	disabled := false
	devs := []config.Device{
		{ID: "a", IP: "10.0.0.1", Persona: config.Persona{Vendor: "cisco"}},
		{ID: "b", IP: "10.0.0.2", Persona: config.Persona{Vendor: "dell"}, Enabled: &disabled},
	}
	m := buildDecoyMACs(devs)
	if len(m) != 1 {
		t.Fatalf("se esperaba 1 MAC, got %d", len(m))
	}
	if _, ok := m["10.0.0.1"]; !ok {
		t.Fatal("falta la IP del dispositivo habilitado")
	}
}
