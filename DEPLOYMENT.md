# Despliegue

Servidor Linux dedicado. Crear entorno sin descargar durante validación, instalar dependencias desde artefactos aprobados, crear `.env` local y mantener servicios opt-in. Laya se despliega localmente con `docker-compose.ai.yml` o un `laya-serve` equivalente; la aplicación conserva fallback cuando no está disponible. No usar `privileged` ni `network_mode: host`.
