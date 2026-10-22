# netpulse-noc

NOC multivendor independiente.

Deception y VMs señuelo fueron retirados; Laya permanece completo. Copie `.env.example` a `.env`, genere secretos únicos y revise `DEPLOYMENT.md`. No se incluyen inventario, usuarios, claves, tokens, bases de datos, backups, ISOs ni datos runtime.

El panel HTML de NetPulse vuelve a ser la interfaz principal en `/` y `/legacy`. Su portada muestra primero el estado real de la red, los equipos afectados y las métricas de inventario; el login y la navegación también fueron mejorados. El estilo de esta portada está aislado en `app/static/overview.css` para no modificar las demás herramientas. React/TypeScript se conserva en `/modern` como alternativa; para activarlo en `/`, usa `NETPULSE_FRONTEND=react make run`. Guía de modos y desarrollo: [docs/frontend-react.md](docs/frontend-react.md).
