COMPOSE ?= docker compose
BENCH_ARGS ?=

.PHONY: up down logs ps test test-backend test-frontend bench bench-local seed shell psql clean verify

up:            ## Start the full stack (http://localhost:5173)
	$(COMPOSE) up -d --build
	@echo "Frontend: http://localhost:5173   Backend: http://localhost:8000 and :8001"

down:
	$(COMPOSE) down

clean:         ## Stop and delete the database volume
	$(COMPOSE) down -v

logs:
	$(COMPOSE) logs -f backend backend-b

ps:
	$(COMPOSE) ps

seed:
	$(COMPOSE) exec backend python manage.py seed_demo

test: test-backend test-frontend

test-backend:  ## Run the backend suite against real PostgreSQL + Redis
	$(COMPOSE) up -d --build postgres redis
	$(COMPOSE) run --rm --no-deps -e RUN_MIGRATIONS=0 backend pytest -v

test-frontend:
	$(COMPOSE) build frontend
	$(COMPOSE) run --rm --no-deps frontend npm test

bench:         ## Restart both servers with benchmark settings, then measure
	DJANGO_SETTINGS_MODULE=config.settings.bench $(COMPOSE) up -d --build backend backend-b
	$(COMPOSE) run --rm --no-deps -e RUN_MIGRATIONS=0 -w /benchmarks backend \
		python -m latency.run --server-a http://backend:8000 --server-b http://backend-b:8000 $(BENCH_ARGS)

bench-local:   ## Benchmark from the host against :8000/:8001 (pip install -r benchmarks/requirements.txt)
	cd benchmarks && python -m latency.run $(BENCH_ARGS)

shell:
	$(COMPOSE) exec backend python manage.py shell

psql:
	$(COMPOSE) exec postgres psql -U sync -d sync_engine

verify:        ## Everything: backend + frontend tests, then the 1/10/50-receiver benchmark
	$(MAKE) test-backend
	$(MAKE) test-frontend
	$(MAKE) bench BENCH_ARGS="--scenario all --receivers 1,10,50 $(BENCH_ARGS)"
	@echo "Results: benchmarks/latency/results/latest.md"
