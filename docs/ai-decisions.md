# NetPulse — Capa de decisiones IA (Laya) y detección de falsos positivos

## Qué es

NetPulse integra un motor de decisiones **"System One"** y lo usa, entre
otras cosas, para **detectar falsos positivos en la red señuelo
(deception)**.

El motor es **Laya** (`convaiinnovations/laya*`, Apache-2.0), la
alternativa **libre, open-weight y multilingüe** a Jev (TypeSafe AI):
recibe un `state` (texto/JSON) + **preguntas tipadas** y devuelve
**respuestas tipadas con probabilidad y confianza** en un solo *forward
pass*. No genera texto, así que no hay nada que parsear ni alucinar.

| Primitiva | Devuelve |
|---|---|
| `noul`   | probabilidad de "sí" |
| `choice` | opción elegida + distribución + confianza |
| `score`  | nivel en una escala + distribución + confianza |

El modelo se sirve por HTTP con `laya-serve` en un contenedor
(`docker-compose.ai.yml`, puerto **8090**) y NetPulse lo consume desde
`app/services/ai/`.

## Arquitectura

```
app/services/ai/
  schemas.py          # primitivas noul/choice/score + Answer/Decision
  decision_svc.py     # fachada: redacción, caché, timeout, gating, métricas, fail-open
  backends/laya_backend.py   # HTTP a laya-serve (/v1/systemone)
  backends/stub_backend.py   # offline/tests

app/services/deception/fp_svc.py   # pregunta a la IA si el evento es benigno
app/services/deception/alert_svc.py# engancha el veredicto antes de notificar
app/routers/ai.py                  # GET /api/ai/status, POST /api/ai/decide
```

## Detección de falsos positivos (deception)

Por cada alerta candidata, `fp_svc.classify()` envía a Laya un `state`
**mínimo y redactado** (evento + contexto: rol/segmento/persona del
señuelo, protocolo, usuario, `command`/`url` truncados, origen con
scope/país/allowlist/blocklist y frecuencias) y estas preguntas:

- `is_benign` (noul) — ¿es tráfico benigno o falso positivo?
- `real_intrusion` (noul) — ¿es un adversario real?
- `severity` (choice) — info/low/medium/high/critical
- `mitre_tactic` (choice) — táctica ATT&CK (`none` si es benigno)
- `should_block` (noul) — sugerencia (advisory)

Comportamiento (*advisory + gating*, fail-open):

- `is_benign ≥ DECEPTION_AI_FP_MIN_CONFIDENCE` → **se suprime** la alerta
  (no notifica, no bloquea) y se registra `deception_alert_suppressed`.
- `real_intrusion` alto → la alerta se dispara **enriquecida** con el
  bloque `ai` (verdict, confianza, severidad, táctica, suggested block).
- IA apagada / error / confianza baja → flujo por reglas sin cambios.

El auto-bloqueo sigue exigiendo `NETPULSE_DECEPTION_AUTOBLOCK_CRITICAL`
**y** confianza suficiente; la IA nunca bloquea por sí sola.

## Arranque

```bash
# 1. Levantar el motor (descarga el checkpoint ~322M la primera vez)
docker compose -f docker-compose.ai.yml up -d --build

# 2. Comprobar
curl -s localhost:8090/health

# 3. Activar la capa IA en NetPulse (.env) y reiniciar la API
NETPULSE_AI_ENABLED=true
```

> En Docker sobre Apple Silicon (arm64) corre en **CPU** (sin MPS).
> Para usar la GPU de Apple, corre `laya-serve` nativo en su propio venv;
> el cliente HTTP no cambia.

> **Memoria de Docker**: cargar `laya-multilingual` (322M parámetros) +
> torch necesita **≥ 6–8 GB** de RAM en la VM de Docker. Con el valor por
> defecto de Colima (3.8 GB) el proceso muere al cargar el modelo. Súbela:
> `colima stop && colima start --memory 8` (Docker Desktop: Settings →
> Resources → Memory). `LAYA_PRELOAD` va en `false` a propósito (con
> `true` el arranque del contenedor falla en este entorno): la primera
> petición carga el modelo (~30–90 s) y las siguientes son rápidas; sube
> `NETPULSE_AI_TIMEOUT` (p. ej. 60) si quieres tolerar esa primera carga.
>
> **Alternativa más rápida en Mac**: correr nativo con la GPU de Apple:
> ```bash
> python3 -m venv .venv-laya && . .venv-laya/bin/activate
> pip install "laya[serve]"
> LAYA_HOST=127.0.0.1 LAYA_PORT=8090 LAYA_MODELS=multilingual \
>   LAYA_DEFAULT_MODEL=multilingual laya-serve
> ```

## Configuración (`.env`)

Ver `.env.example` → sección "Capa de decisiones IA" y "Falsos positivos
de deception con IA".

## Endpoints

- `GET  /api/ai/status` → habilitado, backend, modelo, alcance.
- `POST /api/ai/decide` → decisión genérica (`state` + `questions`).
- `GET  /api/deception/alerts` → alertas (incluyen `ai` si se evaluaron).
- `GET  /api/deception/false-positives` → alertas suprimidas por la IA.

## Seguridad y límites

- **Redacción**: nunca se envían credenciales, `enable_password`, tokens
  ni configs; `command`/`url` se redactan y truncan.
- **Advisory**: la IA aconseja; las acciones destructivas requieren regla
  o política.
- **Fail-open**: `NETPULSE_AI_ENABLED=false` por defecto.

## Calibración (importante)

Laya `multilingual` **viene sin calibrar y es sobre-confiado**. Por eso se
arranca con un umbral conservador (`0.90`). Para mejorar la precisión se
recomienda, en una fase posterior, **reajustar temperaturas** por tipo y
número de opciones con datos etiquetados reales (`laya-evals` /
`fit_abstention_thresholds`). Como límites del modelo: `choice` con <20
opciones y `score` con posible sesgo de posición en multilingüe.
