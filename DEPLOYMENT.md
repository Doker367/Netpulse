# Despliegue

Servidor Linux dedicado. Crear entorno sin descargar durante validación, instalar dependencias desde artefactos aprobados (`requirements.txt` y `requirements-dev.txt` van con versiones pineadas), crear `.env` local y mantener servicios opt-in. Laya se despliega localmente con `docker-compose.ai.yml` o un `laya-serve` equivalente; la aplicación conserva fallback cuando no está disponible. No usar `privileged` ni `network_mode: host`.

## Exposición de red

Publique la API únicamente detrás del reverse proxy con TLS, en la VLAN de gestión. Excepción controlada: `GET /api/metrics` es público por diseño (scrape de Prometheus) y revela identificadores de la infraestructura (`device_id`, `driver`) y métricas del host. Restrinja esa ruta a la IP del servidor Prometheus en el proxy o firewall:

```nginx
# Ejemplo nginx: solo Prometheus (10.0.0.20) puede scrapear
location = /api/metrics {
    allow 10.0.0.20/32;
    deny all;
    proxy_pass http://127.0.0.1:8082;
}
```

Si el firewall ya aísla el puerto de NetPulse a la VLAN de gestión, esta ACL es redundante, pero se recomienda como defensa en profundidad.
