//go:build !packet

// Stub para builds sin modo paquete. Compilar con `-tags packet` para
// habilitar el respondedor ARP/TCP/ICMP (requiere root y libpcap).
package main

import "log"

func startPacketMode(iface string, macByIP map[string]string) {
	log.Printf("[packet] modo paquete no compilado (usar `go build -tags packet`)")
}
