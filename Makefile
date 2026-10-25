# NetPulse / NetMesh — atajos de desarrollo y pruebas.
# Uso: make help

PY := .venv/bin/python
PORT ?= 8082
IMAGE ?= netpulse-noc:local
# Vacío conserva los stacks de laboratorio existentes.
COMPOSE_FILE ?=
DOCKER_URL ?= http://127.0.0.1:$(PORT)

.PHONY: help venv seed run stop smoke test lint go-build docker-build docker-up docker-down docker-smoke ci ci-js ci-shell clean

# HTMLParser evita confundir scripts externos con JavaScript inline.
define CI_JS_CHECK
from html.parser import HTMLParser
from pathlib import Path
import subprocess
import tempfile

class Scripts(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.blocks = []
        self.current = None
    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "script" and "src" not in attrs and attrs.get("type", "").lower() in ("", "text/javascript", "application/javascript", "module"):
            self.current = [attrs.get("type") == "module", ""]
    def handle_data(self, data):
        if self.current is not None:
            self.current[1] += data
    def handle_endtag(self, tag):
        if tag == "script" and self.current is not None:
            self.blocks.append(self.current)
            self.current = None

parser = Scripts()
parser.feed(Path("app/static/dashboard.html").read_text(encoding="utf-8"))
with tempfile.TemporaryDirectory(prefix="netpulse-js-") as temp:
    for index, (module, source) in enumerate(parser.blocks):
        path = Path(temp) / (str(index) + (".mjs" if module else ".js"))
        path.write_text(source, encoding="utf-8")
        subprocess.run(["node", "--check", str(path)], check=True)
files = sorted(Path("app/static/js").rglob("*.js"))
if not files:
    raise SystemExit("ERROR: no hay módulos en app/static/js/")
for path in files:
    subprocess.run(["node", "--check", str(path)], check=True)
print(f"JS OK: {len(parser.blocks)} bloques inline y {len(files)} módulos")
endef
export CI_JS_CHECK

help: ## Muestra esta ayuda
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

venv: ## Crea el venv e instala dependencias
	python3 -m venv .venv && .venv/bin/pip install -q -r requirements-dev.txt

seed: ## Crea usuarios de prueba con contraseñas conocidas
	$(PY) scripts/seed_users.py

run: ## Arranca la API NetPulse en primer plano (requiere .env)
	@test -f .env || { echo "Falta .env: cp .env.example .env y genere secretos (ver docs/ejecucion-local.md)" >&2; exit 1; }
	$(PY) -m uvicorn app.main:app --host 127.0.0.1 --port $(PORT)

stop: ## Detiene API, contenedores y Colima
	bash scripts/stop_all.sh

smoke: ## Pruebas en vivo de la API y los módulos de la Ola 1 (requiere "make run")
	NETPULSE_URL=http://127.0.0.1:$(PORT) bash scripts/smoke_ola1.sh

test: ## Corre la suite de tests
	$(PY) -m pytest tests -q

lint: ## Lint con ruff
	.venv/bin/ruff check app tests scripts

go-build: ## Compila el data plane en Go (+ modo paquete)
	cd . && go build -o bin/engine ./cmd/engine && \
		go build -tags packet -o bin/engine-packet ./cmd/engine && echo "OK"

docker-build: ## Construye la imagen local (IMAGE=netpulse-noc:local)
	@test -f Dockerfile || { echo "Empaquetado bloqueado: falta Dockerfile" >&2; exit 1; }
	docker build -t "$(IMAGE)" .

docker-up: ## Levanta los stacks existentes o el COMPOSE_FILE indicado
ifneq ($(strip $(COMPOSE_FILE)),)
	docker compose -f "$(COMPOSE_FILE)" up -d --build
else
	docker compose -f docker-compose.lab.yml up -d --build
	docker compose -f docker-compose.monitoring.yml up -d
	docker compose -f docker-compose.netbox.yml up -d
	docker compose -f docker-compose.ai.yml up -d --build
endif

docker-down: ## Baja los stacks existentes o el COMPOSE_FILE indicado
ifneq ($(strip $(COMPOSE_FILE)),)
	docker compose -f "$(COMPOSE_FILE)" down
else
	docker compose -f docker-compose.ai.yml down
	docker compose -f docker-compose.netbox.yml down
	docker compose -f docker-compose.monitoring.yml down
	docker compose -f docker-compose.lab.yml down
endif

docker-smoke: ## Comprueba /api/health del contenedor (DOCKER_URL configurable)
	curl --fail --silent --show-error --max-time 15 "$(DOCKER_URL)/api/health"

ci: ## Ejecuta pytest y comprueba sintaxis JavaScript y Bash
	PYTHONPATH=. $(PY) -m pytest tests -q
	$(MAKE) ci-js ci-shell

ci-js: ## Comprueba JS inline y todos los módulos JS recursivamente
	@$(PY) -c "$$CI_JS_CHECK"

ci-shell: ## Comprueba sintaxis de cada script Bash
	@set -e; for script in scripts/*.sh; do bash -n "$$script"; done
	@echo "Bash OK"

down: stop ## Alias de stop

clean: ## Limpia artefactos de runtime (telemetría, logs)
	rm -rf audit/ ./bin/
