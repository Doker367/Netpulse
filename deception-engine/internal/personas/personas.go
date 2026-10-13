// Package personas define el "guión" de los dispositivos señuelo:
// banners, prompts y respuestas a comandos por rol.
package personas

import "strings"

var defaultBanner = map[string]string{
	"pc":       "OpenSSH_8.9",
	"server":   "OpenSSH_8.2p1 Ubuntu-4ubuntu0.5",
	"camera":   "Hikvision-Webs",
	"switch":   "Cisco IOS SSH 1.25",
	"firewall": "FortiGate-60F",
	"router":   "MikroTik RouterOS 7.11",
}

var promptByRole = map[string]string{
	"pc":       "$ ",
	"server":   "# ",
	"camera":   "> ",
	"switch":   "sw-access-01# ",
	"firewall": "fw-edge-01 # ",
	"router":   "[admin@router-core-01] > ",
}

var cmdResponses = map[string]string{
	"id":             "uid=0(root) gid=0(root) groups=0(root)",
	"whoami":         "root",
	"uname -a":       "Linux decoy 5.15.0-91-generic #101-Ubuntu SMP x86_64 GNU/Linux",
	"hostname":       "decoy",
	"pwd":            "/root",
	"ls":             "bin  boot  dev  etc  home  root  tmp  usr  var",
	"cat /etc/passwd": "root:x:0:0:root:/root:/bin/bash\nwww-data:x:33:33:www-data:/var/www:/usr/sbin/nologin",
	"ifconfig":       "eth0: flags=4163<UP,BROADCAST,RUNNING,MULTICAST>  mtu 1500",
	"show version":   "Cisco IOS Software, Version 15.2(4)E",
}

// Banner devuelve el banner del rol (o el override si se indica).
func Banner(role, override string) string {
	if override != "" {
		return override
	}
	if b, ok := defaultBanner[role]; ok {
		return b
	}
	return defaultBanner["pc"]
}

// Prompt devuelve el prompt falso del rol.
func Prompt(role string) string {
	if p, ok := promptByRole[role]; ok {
		return p
	}
	return "$ "
}

var credentials = map[string][][2]string{
	"pc":       {{"admin", "admin"}, {"user", "password"}, {"administrador", "1234"}},
	"server":   {{"root", "root"}, {"root", "toor"}, {"admin", "admin"}},
	"camera":   {{"admin", "admin"}, {"admin", "12345"}, {"admin", ""}},
	"switch":   {{"admin", "admin"}, {"cisco", "cisco"}, {"manager", "manager"}},
	"firewall": {{"admin", "admin"}, {"admin", "Fortinet"}, {"root", "fortinet"}},
	"router":   {{"admin", ""}, {"admin", "admin"}, {"cisco", "cisco"}},
}

// ValidCredential indica si el par usuario/contraseña es un señuelo débil.
func ValidCredential(role, user, pass string) bool {
	for _, c := range credentials[role] {
		if c[0] == user && c[1] == pass {
			return true
		}
	}
	return false
}

// Response devuelve la respuesta simulada a un comando.
func Response(cmd string) string {
	return cmdResponses[strings.TrimSpace(cmd)]
}
