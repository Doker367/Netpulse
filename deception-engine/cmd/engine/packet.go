//go:build packet

// Modo paquete (fidelidad de red): responde ARP, TCP SYN e ICMP por las
// IPs señuelo para que un escaneo (nmap) las vea como dispositivos
// reales. Cada IP responde con la MAC (OUI) de su persona para no delatar
// el host. Requiere privilegios (root) y compilar con `-tags packet`.
package main

import (
	"log"
	"net"
	"sync"

	"github.com/google/gopacket"
	"github.com/google/gopacket/layers"
	"github.com/google/gopacket/pcap"
)

// startPacketMode inicia el respondedor de paquetes sobre una interfaz.
// `macByIP` mapea cada IP señuelo a la MAC que debe usar (OUI del vendor).
// Es bloqueante: se debe invocar en una goroutine.
func startPacketMode(iface string, macByIP map[string]string) {
	if iface == "" {
		log.Printf("[packet] modo paquete sin -iface; omitido")
		return
	}
	if len(macByIP) == 0 {
		log.Printf("[packet] sin IPs señuelo; omitido")
		return
	}

	ifaceObj, err := net.InterfaceByName(iface)
	if err != nil {
		log.Printf("[packet] interfaz %s no encontrada: %v", iface, err)
		return
	}
	srcMAC := ifaceObj.HardwareAddr

	handle, err := pcap.OpenLive(iface, 65536, true, pcap.BlockForever)
	if err != nil {
		log.Printf("[packet] no se pudo abrir %s (¿root?): %v", iface, err)
		return
	}
	defer handle.Close()

	log.Printf("[packet] respondiendo por %d IPs señuelo en %s (MAC por persona)", len(macByIP), iface)

	var mu sync.Mutex
	source := gopacket.NewPacketSource(handle, handle.LinkType())
	for packet := range source.Packets() {
		mu.Lock()
		handlePacket(handle, packet, srcMAC, macByIP)
		mu.Unlock()
	}
}

func macFor(macByIP map[string]string, ip, fallback net.HardwareAddr) net.HardwareAddr {
	if s, ok := macByIP[ip]; ok {
		if hw, err := net.ParseMAC(s); err == nil {
			return hw
		}
	}
	return fallback
}

func handlePacket(handle *pcap.Handle, packet gopacket.Packet, srcMAC net.HardwareAddr, macByIP map[string]string) {
	ethLayer := packet.Layer(layers.LayerTypeEthernet)
	if ethLayer == nil {
		return
	}
	eth := ethLayer.(*layers.Ethernet)

	// ARP: responder "who-has" de nuestras IPs (con la MAC del señuelo).
	if arpLayer := packet.Layer(layers.LayerTypeARP); arpLayer != nil {
		arp := arpLayer.(*layers.ARP)
		targetIP := net.IP(arp.DstProtAddress).String()
		if arp.Operation == layers.ARPRequest {
			if _, ok := macByIP[targetIP]; ok {
				replyMAC := macFor(macByIP, targetIP, srcMAC)
				reply := &layers.ARP{
					AddrType:          arp.AddrType,
					Protocol:          arp.Protocol,
					HwAddressSize:     arp.HwAddressSize,
					ProtAddressSize:   arp.ProtAddressSize,
					Operation:         layers.ARPReply,
					SourceHwAddress:   replyMAC,
					SourceProtAddress: arp.DstProtAddress,
					DstHwAddress:      arp.SourceHwAddress,
					DstProtAddress:    arp.SourceProtAddress,
				}
				ethResp := &layers.Ethernet{
					SrcMAC: replyMAC, DstMAC: eth.SrcMAC,
					EthernetType: layers.EthernetTypeARP,
				}
				_ = send(handle, ethResp, reply)
			}
		}
		return
	}

	ipLayer := packet.Layer(layers.LayerTypeIPv4)
	if ipLayer == nil {
		return
	}
	ip := ipLayer.(*layers.IPv4)
	dst := ip.DstIP.String()
	if _, ok := macByIP[dst]; !ok {
		return
	}
	replyMAC := macFor(macByIP, dst, srcMAC)

	// ICMP echo → echo reply.
	if icmpLayer := packet.Layer(layers.LayerTypeICMPv4); icmpLayer != nil {
		icmp := icmpLayer.(*layers.ICMPv4)
		if icmp.TypeCode.Type() == layers.ICMPv4TypeEchoRequest {
			reply := &layers.ICMPv4{
				TypeCode: layers.CreateICMPv4TypeCode(layers.ICMPv4TypeEchoReply, 0),
				Id:       icmp.Id, Seq: icmp.Seq,
			}
			sendIPv4(handle, eth, ip, reply, layers.IPProtocolICMPv4, replyMAC)
		}
		return
	}

	// TCP SYN → SYN-ACK (hace ver los puertos como abiertos).
	if tcpLayer := packet.Layer(layers.LayerTypeTCP); tcpLayer != nil {
		tcp := tcpLayer.(*layers.TCP)
		if tcp.SYN && !tcp.ACK {
			reply := &layers.TCP{
				SrcPort: tcp.DstPort, DstPort: tcp.SrcPort,
				Seq: 0, Ack: tcp.Seq + 1,
				SYN: true, ACK: true, Window: 64240,
			}
			sendIPv4(handle, eth, ip, reply, layers.IPProtocolTCP, replyMAC)
		}
	}
}

func sendIPv4(handle *pcap.Handle, eth *layers.Ethernet, ip *layers.IPv4,
	payload gopacket.SerializableLayer, proto layers.IPProtocol, srcMAC net.HardwareAddr) {
	ipResp := &layers.IPv4{
		Version: 4, IHL: 5, TTL: 64,
		SrcIP: ip.DstIP, DstIP: ip.SrcIP, Protocol: proto,
	}
	ethResp := &layers.Ethernet{
		SrcMAC: srcMAC, DstMAC: eth.SrcMAC,
		EthernetType: layers.EthernetTypeIPv4,
	}
	_ = send(handle, ethResp, ipResp, payload)
}

func send(handle *pcap.Handle, ls ...gopacket.SerializableLayer) error {
	buf := gopacket.NewSerializeBuffer()
	opts := gopacket.SerializeOptions{FixLengths: true, ComputeChecksums: true}
	if err := gopacket.SerializeLayers(buf, opts, ls...); err != nil {
		return err
	}
	return handle.WritePacketData(buf.Bytes())
}
