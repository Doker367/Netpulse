# CI y empaquetado

## Qué se ejecuta

- [CI](<../.github/workflows/ci.yml>): cada push a `main` y PR dirigido a
  `main`, en Ubuntu, con Python 3.12, Node.js 22 y caché pip. Instala
  [requirements-dev.txt](<../requirements-dev.txt>), ejecuta
  `ruff check app tests scripts` (ruff está declarado), y `make ci PY=python`.
  Pyright permanece en un job informativo, no bloqueante.
- `make ci`: `PYTHONPATH=. python -m pytest tests -q`, extracción y
  `node --check` de cada script inline ejecutable del
  [panel](<../app/static/dashboard.html>), comprobación recursiva de todos los
  módulos JS (30 en esta base), y `bash -n` para cada script shell.
  El panel actual no tiene bloques JS inline; se reporta cero, no se inventa
  una comprobación. No se encontró un extractor inline en los tests de esta
  base; se usa HTMLParser, excluyendo scripts con `src` y bloques de datos.
- [Docker](<../.github/workflows/docker.yml>): push a `main`, PR a `main` y
  tags `v*`. Comprueba los archivos requeridos, valida compose y construye
  con Buildx (`docker/build-push-action`) y caché de GitHub Actions.
  Un PR, incluido uno de un fork, nunca inicia sesión en GHCR ni publica.

## Reproducir localmente

Requisitos: Python 3.12, Node.js 22, Bash y Make; Docker con Compose v2 para
el empaquetado.

```sh
make venv
make ci
make lint
make help
```

Para reutilizar un entorno ya instalado sin modificarlo:

```sh
make ci PY=/Users/doker/netpulse-noc/.venv/bin/python
```

`make ci` no incluye ruff ni Pyright; el workflow añade esas comprobaciones.
La instalación limpia de dependencias y la ejecución de Actions son
validaciones distintas de reutilizar un entorno local.

## Imagen y publicación

```sh
make docker-build IMAGE=netpulse-noc:local
# Requiere que el compose de producción ya esté integrado:
docker compose -f deploy/docker-compose.prod.yml config --quiet
make docker-up COMPOSE_FILE=deploy/docker-compose.prod.yml
make docker-smoke DOCKER_URL=http://127.0.0.1:8082
make docker-down COMPOSE_FILE=deploy/docker-compose.prod.yml
```

Sin `COMPOSE_FILE`, `docker-up` y `docker-down` conservan sus stacks de
laboratorio, monitoreo, NetBox e IA. El smoke comprueba HTTP en `/api/health`;
no sustituye pruebas funcionales ni demuestra por sí solo qué imagen sirve
ese puerto. Ajustar URL/puerto a la configuración real.

La publicación ocurre **solo al hacer push de un tag `v*`**, después de que
el job de construcción y validación termine correctamente. Utiliza
`GITHUB_TOKEN`; solamente ese job tiene `packages: write`, junto con
`contents: read`. Publica `ghcr.io/<owner>/<repo>:<tag>` en minúsculas para
owner/repo, sin mover `latest`. El tag debe ser válido también como tag Docker
(por ejemplo `v1.2.3`). No se ejecutan pushes desde esta tarea.

El workflow CI de Python se dispara por ramas/PRs, no por tags: el proceso de
release debe elegir un commit de `main` cuyo CI esté verde. No se afirma que
Actions ya haya ejecutado estos workflows.

## Desplegar un tag

1. Elegir un tag publicado con CI verde. En el host, autenticarse con
   `docker login ghcr.io` si el paquete es privado (token de lectura).
2. Descargar `docker pull ghcr.io/<owner>/<repo>:v1.2.3`.
3. En la configuración de producción del host, establecer `image:` del
   servicio API a esa referencia exacta, preferiblemente su digest. Preparar
   secretos, volúmenes y variables requeridas por el compose; no guardarlos
   en Git. El nombre del servicio y las variables se deben tomar del compose
   real, no se presuponen aquí.
4. Ejecutar `docker compose -f deploy/docker-compose.prod.yml config --quiet`
   y luego `docker compose -f deploy/docker-compose.prod.yml up -d --no-build`.
   No usar `make docker-up` para este caso: su `--build` es para builds locales.
5. Ejecutar `make docker-smoke DOCKER_URL=https://<host>` y revisar logs y salud.
   Para rollback, repetir con la referencia anterior y conservar los datos.

## Bloqueo de esta base

No existen `Dockerfile`, `deploy/docker-compose.prod.yml` ni `docs/DOCKER.md`
en esta revisión. Son responsabilidad de otra tarjeta y no se crean aquí.
El workflow falla explícitamente si faltan los dos primeros; no omite el
build ni transforma ese bloqueo en éxito. Hasta su integración no se pueden
validar el build, la configuración, las variables requeridas ni el despliegue
real. La receta de despliegue anterior es el procedimiento previsto, no una
prueba de que exista ya un stack de producción operativo.

## Validación local de esta tarjeta

- `make ci PY=/Users/doker/netpulse-noc/.venv/bin/python`: éxito, suite de
  358 tests, 30 módulos JS, cero bloques inline y todos los scripts Bash.
  Una advertencia de deprecación Starlette/httpx.
- `ruff check app tests scripts`: éxito. Ambos YAML cargados con
  `yaml.safe_load`: éxito. `git diff --check`: sin errores.
- Entorno usado: Python 3.12.14 y Node.js 26.7.0; no se ejecutó localmente
  Node.js 22 ni una instalación limpia de las dependencias.
- Verificado el fallo explícito de `make docker-build` al faltar Dockerfile.
  Compose de producción, build real, smoke, GHCR, Pyright y ejecución remota
  de Actions no validados.
