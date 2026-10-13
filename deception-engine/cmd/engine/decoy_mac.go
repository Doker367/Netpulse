// MAC de los señuelos: OUI del fabricante (spoofing) por IP.
//
// Para que el análisis de fabricantes de un atacante no delate la trampa,
// cada IP señuelo responde con una MAC acorde a su persona (prefijo OUI real
// del vendor). Si la persona define `mac`, se respeta esa.
package main

import (
	"fmt"
	"net"
	"strings"

	"netpulse/deception-engine/internal/config"
)

// Prefijos OUI por fabricante (reales y representativos).
var vendorOUI = map[string]string{
	"cisco":     "00:1b:0c",
	"hikvision": "bc:ba:e1",
	"dahua":     "3c:ef:8c",
	"axis":      "ac:cc:8e",
	"dell":      "00:14:22",
	"mikrotik":  "74:4d:28",
	"fortinet":  "70:4c:a5",
	"linux":     "02:00:00",
	"generic":   "02:00:00",
}

func ouiFor(vendor string) string {
	if oui, ok := vendorOUI[strings.ToLower(strings.TrimSpace(vendor))]; ok {
		return oui
	}
	return vendorOUI["generic"]
}

// decoyMAC devuelve la MAC de la IP señuelo: la de la persona si existe,
// o una sintetizada (OUI del fabricante + últimos octetos de la IP).
func decoyMAC(persona config.Persona, ip string) string {
	if mac := strings.TrimSpace(persona.MAC); mac != "" {
		return mac
	}
	suffix := "00:00:01"
	if v4 := net.ParseIP(ip).To4(); v4 != nil {
		suffix = fmt.Sprintf("%02x:%02x:%02x", v4[1], v4[2], v4[3])
	}
	return ouiFor(persona.Vendor) + ":" + suffix
}

// buildDecoyMACs mapea IP → MAC de todos los dispositivos habilitados.
func buildDecoyMACs(devices []config.Device) map[string]string {
	out := make(map[string]string, len(devices))
	for _, d := range devices {
		if d.IsEnabled() && d.IP != "" {
			out[d.IP] = decoyMAC(d.Persona, d.IP)
		}
	}
	return out
}
