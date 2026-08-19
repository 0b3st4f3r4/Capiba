.PHONY: help install install-dev test test-slow test-cov lint format format-check typecheck security clean build build-gpu build-airflow helm-upgrade publish-artifacts init-buckets ensure-port-forward dbt-run dbt-test dbt-docs pre-commit port-forward port-forward-stop port-forward-status ingest-mock rollout-airflow bump-version cluster-start cluster-stop cluster-remove cluster-status dashboard-token

PYTHON := python3
VENV := .venv
PIP := $(VENV)/bin/pip
PYTEST := $(VENV)/bin/pytest
RUFF := $(VENV)/bin/ruff
ISORT := $(VENV)/bin/isort
MYPY := $(VENV)/bin/mypy
PYRIGHT := $(VENV)/bin/basedpyright
BANDIT := $(VENV)/bin/bandit
PRE_COMMIT := $(VENV)/bin/pre-commit
# Falls back to sudo when the user is not in the docker group
DOCKER := $(shell docker info >/dev/null 2>&1 && echo docker || echo sudo docker)

help: ## Shows this help message
	@grep -E '^[a-zA-Z0-9_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'

$(VENV)/bin/activate: pyproject.toml
	$(PYTHON) -m venv $(VENV)
	$(PIP) install --upgrade pip
	$(PIP) install -e ".[dev,airflow]"
	@touch $(VENV)/bin/activate

install: $(VENV)/bin/activate ## Installs the project, dev and Airflow dependencies in the venv

install-dev: install ## Alias for install (used by scripts/setup.sh)

test: $(VENV)/bin/activate ## Runs the fast test suite (unit; slow batteries skipped — CAPIBA_SLOW=1 to include)
	$(PYTEST) tests/ -v --tb=short

test-slow: $(VENV)/bin/activate ## Runs only the slow regime tests (detection batteries)
	CAPIBA_SLOW=1 $(PYTEST) tests/ -v --tb=short -m slow

test-cov: $(VENV)/bin/activate ## Runs the full test suite with coverage (floor: 85%; includes slow batteries)
	CAPIBA_SLOW=1 $(PYTEST) tests/ -v --tb=short --cov=src/capiba --cov-report=term-missing --cov-report=html

lint: $(VENV)/bin/activate sort-imports-check ## Checks lint with ruff and import order
	$(RUFF) check src/ tests/ scripts/

format: $(VENV)/bin/activate ## Formats the code with ruff and sorts imports
	$(ISORT) src/ tests/ scripts/
	$(RUFF) format src/ tests/ scripts/

format-check: $(VENV)/bin/activate ## Checks formatting with ruff
	$(RUFF) format --check src/ tests/ scripts/

sort-imports: $(VENV)/bin/activate ## Sorts imports with isort
	$(ISORT) src/ tests/ scripts/

sort-imports-check: $(VENV)/bin/activate ## Checks import order with isort
	$(ISORT) --check-only --diff src/ tests/ scripts/

typecheck: $(VENV)/bin/activate ## Runs type checking with mypy and basedpyright
	$(MYPY) src/
	$(PYRIGHT)

security: $(VENV)/bin/activate ## Runs security analysis with bandit
	$(BANDIT) -r src/

pre-commit: $(VENV)/bin/activate ## Installs and runs the pre-commit hooks
	$(PRE_COMMIT) install
	$(PRE_COMMIT) run --all-files

clean: ## Removes build artifacts, caches and reports
	rm -rf $(VENV) .pytest_cache .ruff_cache .coverage htmlcov bandit-report.json .mypy_cache
	find . -type d -name __pycache__ -exec rm -rf {} +
	find . -type f -name '*.pyc' -delete

build: ## Builds the API Docker image
	$(DOCKER) build -t capiba/api:0.1.0 .

build-gpu: ## Builds the API Docker image with PyTorch GPU support
	$(DOCKER) build -f Dockerfile.gpu -t capiba/api-gpu:0.1.0 .

build-airflow: ## Builds the Airflow Docker image (needed when dependencies change)
	$(DOCKER) build -f Dockerfile.airflow -t capiba/airflow:0.1.0 .

helm-upgrade: ## Upgrades the Helm chart on the local cluster with Airflow enabled (reads .env)
	./scripts/helm-upgrade.sh

publish-artifacts: $(VENV)/bin/activate ## Publishes code (src/), DAGs and the dbt project to MinIO, no image rebuild needed
	$(VENV)/bin/python scripts/publish_artifacts.py

ensure-port-forward: ## Ensures cluster port-forwards are active (idempotent)
	./scripts/port-forward.sh start

init-buckets: $(VENV)/bin/activate ensure-port-forward ## Creates the MinIO buckets and the Lakekeeper Iceberg warehouses (idempotent)
	$(VENV)/bin/python scripts/init_buckets.py

dbt-run: $(VENV)/bin/activate ## Builds the gold Iceberg marts with dbt (needs the cluster port-forwards)
	$(VENV)/bin/dbt run --project-dir dbt --profiles-dir dbt

dbt-test: $(VENV)/bin/activate ## Runs the dbt source/model tests
	$(VENV)/bin/dbt test --project-dir dbt --profiles-dir dbt

dbt-docs: $(VENV)/bin/activate ## Generates and serves the dbt docs (catalog + lineage of the gold marts)
	$(VENV)/bin/dbt docs generate --project-dir dbt --profiles-dir dbt
	$(VENV)/bin/dbt docs serve --project-dir dbt --profiles-dir dbt

port-forward: ## Starts port-forwards for the cluster services (api, minio, airflow, marquez, ...)
	./scripts/port-forward.sh start

port-forward-stop: ## Stops the cluster port-forwards
	./scripts/port-forward.sh stop

port-forward-status: ## Shows which port-forwards are active
	./scripts/port-forward.sh status

ingest-mock: $(VENV)/bin/activate ## Runs the ingestion pipeline offline with mock sources, persisting to the lake
	$(VENV)/bin/python scripts/ingestion.py --source both --mock --persist

rollout-airflow: ## Restarts the Airflow deployment (needed after publishing src/ changes)
	kubectl rollout restart deploy/capiba-airflow -n capiba

bump-version: ## Updates the project version in pyproject, chart, Makefile and API (usage: make bump-version VERSION=0.2.0)
	$(PYTHON) scripts/bump_version.py $(VERSION)

cluster-start: ## Starts the native k3s cluster, Traefik, capiba chart and Headlamp
	./scripts/cluster.sh start

cluster-stop: ## Stops the native k3s cluster (does not remove data)
	./scripts/cluster.sh stop

cluster-remove: ## Stops and removes the native k3s cluster (destructive)
	./scripts/cluster.sh remove

cluster-status: ## Lists the pods of the capiba namespace
	kubectl get pods -n capiba

dashboard-token: ## Prints the login token for the Headlamp dashboard (http://localhost:4466)
	@kubectl get secret headlamp-admin-token -n headlamp -o jsonpath='{.data.token}' | base64 -d; echo
