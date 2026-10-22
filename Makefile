# NetPulse / NetMesh — atajos de desarrollo y pruebas.
# Uso: make help

PY := .venv/bin/python
PORT ?= 8082

.PHONY: help venv seed run stop smoke test lint ui-install ui-build ui-check go-build docker-up docker-down clean

help: ## Muestra esta ayuda
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

venv: ## Crea el venv e instala dependencias
	python3 -m venv .venv && .venv/bin/pip install -q -r requirements-dev.txt

seed: ## Crea usuarios de prueba con contraseñas conocidas
	$(PY) scripts/seed_users.py

ui-install: ## Instala dependencias del frontend React (Node + pnpm)
	cd frontend && pnpm install --frozen-lockfile

ui-build: ## Compila React/TypeScript para servirlo desde FastAPI
	cd frontend && pnpm run build

ui-check: ## Comprueba tipos del frontend sin generar archivos
	cd frontend && pnpm run typecheck

run: ## Arranca la API NetPulse en primer plano (requiere .env)
	@test -f .env || { echo "Falta .env: cp .env.example .env y genere secretos (ver docs/ejecucion-local.md)" >&2; exit 1; }
	$(PY) -m uvicorn app.main:app --host 127.0.0.1 --port $(PORT)

stop: ## Detiene API, contenedores y Colima
	bash scripts/stop_all.sh

smoke: ## Comprueba que la API responde (requiere "make run" en otra terminal)
	@curl -fsS http://127.0.0.1:$(PORT)/api/health && echo

test: ## Corre la suite de tests
	$(PY) -m pytest tests -q

lint: ## Lint con ruff
	.venv/bin/ruff check app tests scripts

go-build: ## Compila el data plane en Go (+ modo paquete)
	cd . && go build -o bin/engine ./cmd/engine && \
		go build -tags packet -o bin/engine-packet ./cmd/engine && echo "OK"

docker-up: ## Levanta laboratorio + monitoreo + NetBox + IA (Docker)
	docker compose -f docker-compose.lab.yml up -d --build
	docker compose -f docker-compose.monitoring.yml up -d
	docker compose -f docker-compose.netbox.yml up -d
	docker compose -f docker-compose.ai.yml up -d --build

docker-down: ## Baja todos los stacks Docker
	docker compose -f docker-compose.ai.yml down
	docker compose -f docker-compose.netbox.yml down
	docker compose -f docker-compose.monitoring.yml down
	docker compose -f docker-compose.lab.yml down

down: stop ## Alias de stop

clean: ## Limpia artefactos de runtime (telemetría, logs)
	rm -rf audit/ ./bin/
