# NetPulse / NetMesh — atajos de desarrollo y pruebas.
# Uso: make help

PY := .venv/bin/python
PORT ?= 8082

.PHONY: help venv seed run smoke test lint go-build docker-up down clean

help: ## Muestra esta ayuda
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

venv: ## Crea el venv e instala dependencias
	python3 -m venv .venv && .venv/bin/pip install -q -r requirements-dev.txt

seed: ## Crea usuarios de prueba con contraseñas conocidas
	$(PY) scripts/seed_users.py

run: ## Arranca NetPulse con deception (dev, primer plano)
	bash scripts/start_deception.sh

smoke: ## Smoke test end-to-end de la red señuelo (sin Docker)
	bash scripts/smoke_deception.sh

test: ## Corre la suite de tests
	$(PY) -m pytest tests -q

lint: ## Lint con ruff
	.venv/bin/ruff check app tests scripts

go-build: ## Compila el data plane en Go (+ modo paquete)
	cd deception-engine && go build -o bin/engine ./cmd/engine && \
		go build -tags packet -o bin/engine-packet ./cmd/engine && echo "OK"

docker-up: ## Levanta honeypots + monitoreo en Docker
	docker compose -f docker-compose.deception.yml up -d --build
	docker compose -f docker-compose.monitoring.yml up -d

down: ## Detiene todo (API, engines, Docker, Colima)
	bash scripts/stop_all.sh

clean: ## Limpia artefactos de runtime (telemetría, logs)
	rm -rf deception/ audit/ deception-engine/bin/
