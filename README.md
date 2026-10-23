# netpulse-noc

NOC multivendor independiente.

Deception y VMs señuelo fueron retirados; Laya permanece completo. Copie `.env.example` a `.env`, genere secretos únicos y revise `DEPLOYMENT.md`. No se incluyen inventario, usuarios, claves, tokens, bases de datos, backups, ISOs ni datos runtime.

El panel HTML de NetPulse es la única interfaz, disponible en `/` y en `/legacy` para enlaces existentes. Su portada muestra el estado real de la red, los equipos afectados y las métricas de inventario; el login y la navegación también fueron mejorados. El estilo de la portada está aislado en `app/static/overview.css` para no modificar las demás herramientas. Guía de uso y desarrollo: [docs/panel-html.md](docs/panel-html.md).
