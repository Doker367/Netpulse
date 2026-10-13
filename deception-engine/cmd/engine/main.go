// deception-engine — data plane de la red señuelo de NetPulse.
//
// Levanta listeners de servicio (SSH/HTTP/Telnet/RTSP/SMB/SNMP) a partir
// de la misma topología (network.yaml) que el motor nativo de Python y
// reporta cada interacción a NetPulse vía /api/deception/ingest.
//
// Uso:
//
//	go run ./cmd/engine -config ../../config/deception/network.dev.yaml \
//	    -ingest http://127.0.0.1:8082/api/deception/ingest -token demo-token
package main

import (
	"flag"
	"log"
	"os"
	"os/signal"
	"syscall"

	"netpulse/deception-engine/internal/config"
	"netpulse/deception-engine/internal/ingest"
	"netpulse/deception-engine/internal/listeners"
)

func main() {
	cfgPath := flag.String("config", "config/deception/network.yaml", "red señuelo (YAML)")
	ingestURL := flag.String("ingest", "http://127.0.0.1:8082/api/deception/ingest", "endpoint de ingest de NetPulse")
	token := flag.String("token", "", "token X-Deception-Token")
	bind := flag.String("bind", "", "host de bind (vacío = IP del dispositivo)")
	packet := flag.Bool("packet", false, "habilitar modo paquete (ARP/TCP/ICMP; requiere -tags packet y root)")
	iface := flag.String("iface", "", "interfaz para el modo paquete (ej. deception0)")
	flag.Parse()

	netCfg, err := config.Load(*cfgPath)
	if err != nil {
		log.Fatalf("deception-engine: %v", err)
	}

	cli := ingest.New(*ingestURL, *token)
	mgr := listeners.NewManager()

	started, failed := mgr.StartAll(netCfg, *bind, cli)
	log.Printf("deception-engine: %d listeners activos, %d fallos (ingest=%s)",
		started, failed, *ingestURL)
	if started == 0 {
		log.Fatalf("deception-engine: no se levantó ningún listener")
	}

	if *packet {
		macs := buildDecoyMACs(netCfg.Devices)
		go startPacketMode(*iface, macs)
	}

	sig := make(chan os.Signal, 1)
	signal.Notify(sig, syscall.SIGINT, syscall.SIGTERM)
	<-sig

	log.Printf("deception-engine: deteniendo (%d listeners)...", mgr.Count())
	mgr.StopAll()
	log.Printf("deception-engine: detenido")
}
