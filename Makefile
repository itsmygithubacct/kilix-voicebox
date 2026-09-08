# Voicebox Makefile
# Unix-only (macOS/Linux). Windows users should use WSL.

SHELL := /bin/bash
.DEFAULT_GOAL := help

# Directories
BACKEND_DIR := kilix_voicebox_backend
TAURI_DIR := tauri
WEB_DIR := web
APP_DIR := app
BACKEND_PKG := kilix_voicebox_backend
BUN ?= bun

# Python dependencies are resolved only by the committed uv lock.
PYTHON := $(shell command -v python3.12 2>/dev/null || command -v python3.13 2>/dev/null || echo python3)
VENV := $(CURDIR)/.venv
VENV_BIN := $(VENV)/bin
PYTHON_VENV := $(VENV_BIN)/python

# Colors for output
BLUE := \033[0;34m
GREEN := \033[0;32m
YELLOW := \033[0;33m
NC := \033[0m # No Color

.PHONY: help
help: ## Show this help message
	@echo -e "$(BLUE)Voicebox$(NC) - Development Commands"
	@echo ""
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  $(GREEN)%-20s$(NC) %s\n", $$1, $$2}'

# =============================================================================
# SETUP
# =============================================================================

.PHONY: setup setup-js setup-python sync-python setup-rust

setup: setup-js setup-python ## Full project setup (all dependencies)
	@echo -e "$(GREEN)✓ Setup complete!$(NC)"
	@echo -e "  Run $(YELLOW)make dev$(NC) to start development servers"

setup-js: ## Install JavaScript dependencies (bun)
	@echo -e "$(BLUE)Installing JavaScript dependencies...$(NC)"
	bun install --frozen-lockfile

setup-python: ## Set up the locked Python environment
	@echo -e "$(BLUE)Installing Python dependencies...$(NC)"
	uv sync --frozen --group dev
	@echo -e "$(GREEN)✓ Python environment ready$(NC)"

sync-python: setup-python ## Alias for locked Python environment setup

$(VENV)/bin/activate:
	uv sync --frozen --group dev

setup-rust: ## Verify the release-selected Rust toolchain is already installed
	@command -v rustc >/dev/null 2>&1 || { \
		echo "Rust is unavailable; install the release-selected toolchain out of band"; \
		exit 1; \
	}

# =============================================================================
# DEVELOPMENT
# =============================================================================

.PHONY: dev dev-backend dev-frontend dev-web

dev: ## Start backend + desktop app (parallel)
	@echo -e "$(BLUE)Starting development servers...$(NC)"
	@echo -e "$(YELLOW)Note: If Tauri fails, run 'make build-server' first or use separate terminals$(NC)"
	@trap 'kill 0' EXIT; \
		$(MAKE) dev-backend & \
		sleep 2 && $(MAKE) dev-frontend & \
		wait

dev-backend: ## Start FastAPI backend server
	@echo -e "$(BLUE)Starting backend server on http://localhost:17493$(NC)"
	@test "$${#KILIX_VOICEBOX_SESSION_TOKEN}" -eq 64 || { \
		echo "Set KILIX_VOICEBOX_SESSION_TOKEN to 32 random bytes in lowercase hex"; \
		exit 1; \
	}
	uv run --frozen uvicorn kilix_voicebox_backend.main:app --reload --host 127.0.0.1 --port 17493 --no-access-log

dev-frontend: ## Start Tauri desktop app
	@echo -e "$(BLUE)Starting Tauri desktop app...$(NC)"
	bun run dev

dev-web: ## Start backend + web app (parallel)
	@echo -e "$(BLUE)Starting web development servers...$(NC)"
	@trap 'kill 0' EXIT; \
		$(MAKE) dev-backend & \
		sleep 2 && cd $(WEB_DIR) && bun run dev & \
		wait

# =============================================================================
# BUILD
# =============================================================================

.PHONY: build build-server build-tauri build-web sidecar-gate build-sidecar \
	build-sidecar-and-package

# `build-server` publishes the sidecar and `build-tauri` packages it, so they
# were never independent: as plain prerequisites `make -j build` could package
# while publication was still in flight. They are separate serial sub-makes
# here, and the Tauri build hook independently refuses to reach a package step
# without a passing sidecar gate, so no route can package a sidecar this build
# did not publish and verify.
build: ## Build everything (sidecar + verified resources + desktop app)
	@$(MAKE) --no-print-directory build-sidecar-and-package
	@echo -e "$(GREEN)✓ Build complete!$(NC)"

# One producer invocation, then packaging, in one process. The packaging step
# is told which build the gate passed for, so the Tauri hook verifies that work
# instead of repeating the producer.
build-sidecar-and-package:
	$(PYTHON) -I tools/build_sidecar.py --package 'cd $(TAURI_DIR) && bun run tauri build'

# The whole bridge: a new invocation identity, the clean product commit and
# tree, every explicit selected input, one producer run, then the consumer --
# and only then packaging. It refuses and explains rather than choosing inputs.
build-sidecar: ## Build and verify the sidecar from explicit selected inputs
	@echo -e "$(BLUE)Building the sidecar from explicit selected inputs...$(NC)"
	$(PYTHON) -I tools/build_sidecar.py

# Developer path: run the collector directly with inputs you name yourself. It
# produces and publishes the pair but runs no consumer and prepares no Tauri
# resources, so it is for producing and inspecting a sidecar, never for
# packaging a release. Use `make build` for that.
build-server: ## Collector only, explicit inputs (no consumer, not packageable)
	@echo -e "$(BLUE)Building server binary (collector only)...$(NC)"
	@echo -e "$(YELLOW)No consumer runs on this path; it does not make a bundle packageable.$(NC)"
	./scripts/build-server.sh $(BUILD_SERVER_ARGS)

# The same gate the Tauri build hook runs, exposed for operators and controls.
# It verifies the installed sidecar identity and prepares the bundle resources;
# it never invokes the producer, so running it costs no rebuild.
sidecar-gate: ## Verify the installed sidecar identity and prepare Tauri resources
	@echo -e "$(BLUE)Verifying the installed sidecar identity...$(NC)"
	$(PYTHON) -I tools/tauri_build_gate.py

# `bun run tauri build` runs the configured build hook, which is that gate, so
# this target reaches it exactly once rather than duplicating the work.
build-tauri: ## Build Tauri desktop app (gated by the sidecar hook)
	@echo -e "$(BLUE)Building Tauri desktop app...$(NC)"
	cd $(TAURI_DIR) && bun run tauri build

build-web: ## Build web app
	@echo -e "$(BLUE)Building web app...$(NC)"
	cd $(WEB_DIR) && bun run build
	@echo -e "$(GREEN)✓ Web build output in $(WEB_DIR)/dist/$(NC)"

# =============================================================================
# DATABASE & API
# =============================================================================

.PHONY: db-init db-reset generate-api

db-init: $(VENV)/bin/activate ## Initialize SQLite database
	@echo -e "$(BLUE)Initializing database...$(NC)"
	cd $(BACKEND_DIR) && $(PYTHON_VENV) -c "from database import init_db; init_db()"
	@echo -e "$(GREEN)✓ Database created at $(BACKEND_DIR)/data/voicebox.db$(NC)"

db-reset: ## Reset database (delete and reinitialize)
	@echo -e "$(YELLOW)Resetting database...$(NC)"
	rm -f $(BACKEND_DIR)/data/voicebox.db
	$(MAKE) db-init

generate-api: ## Generate TypeScript API client from OpenAPI schema
	@echo -e "$(BLUE)Generating API client...$(NC)"
	./scripts/generate-api.sh
	@echo -e "$(GREEN)✓ API client generated in $(APP_DIR)/src/lib/api/$(NC)"

# =============================================================================
# CODE QUALITY
# =============================================================================

.PHONY: lint format typecheck check check-backend check-frontend check-kilix

lint: ## Run linter (Biome)
	@echo -e "$(BLUE)Linting...$(NC)"
	bun run lint

format: ## Format code (Biome)
	@echo -e "$(BLUE)Formatting...$(NC)"
	bun run format

typecheck: ## Run TypeScript type checking
	@echo -e "$(BLUE)Type checking...$(NC)"
	bun run tsc --noEmit

check-kilix: ## Verify the provider-only Voicebox boundary
	@set -euo pipefail; bootstrap_output="$$(env \
		-u GIT_ALTERNATE_OBJECT_DIRECTORIES \
		-u GIT_COMMON_DIR -u GIT_CONFIG_COUNT -u GIT_CONFIG_PARAMETERS \
		-u GIT_DIR -u GIT_INDEX_FILE -u GIT_NAMESPACE \
		-u GIT_OBJECT_DIRECTORY -u GIT_QUARANTINE_PATH \
		-u GIT_REPLACE_REF_BASE -u GIT_SHALLOW_FILE -u GIT_WORK_TREE \
		GIT_NO_REPLACE_OBJECTS=1 git -C "$(CURDIR)" show \
		HEAD:.kilix-import/verify_import.py | $(PYTHON) -I -S -)"; \
		[[ "$$bootstrap_output" == "VOICEBOX_CURATED_IMPORT: PASS ("* ]]; \
		[[ "$$bootstrap_output" != *$$'\n'* ]]; \
		printf '%s\n' "$$bootstrap_output"
	uv run --frozen --isolated python -I -m unittest discover -s .kilix-import -p 'test_*.py' -v
	uv run --frozen --isolated python -I tools/check_kilix_boundary.py
	uv run --frozen --isolated python -I tools/check_import_inventory.py

check-backend: check-kilix ## Run backend compile and unit controls
	@$(MAKE) --no-print-directory assert-backend-sources
	uv run --frozen --isolated python -I -m compileall -q $(BACKEND_PKG)
	uv run --frozen --isolated --group dev ruff check $(BACKEND_PKG) tools
	uv run --frozen --isolated --group dev python -I -m unittest discover -s $(BACKEND_PKG)/tests -t . -p 'test_*.py' -v

# `compileall` and `unittest discover` both exit 0 on a directory that does not
# exist -- so when the package was renamed away from `backend`, the compile
# control silently became a no-op that reported success, and only ruff's E902
# revealed it. This asserts the thing those controls assume, and prints the
# denominator it is asserting over, so an empty or missing package fails here
# instead of passing downstream.
.PHONY: assert-backend-sources
assert-backend-sources:
	@test -d "$(BACKEND_PKG)" || { \
		printf 'FAIL package directory does not exist: %s\n' "$(BACKEND_PKG)" >&2; exit 1; }
	@test -d "$(BACKEND_PKG)/tests" || { \
		printf 'FAIL test directory does not exist: %s/tests\n' "$(BACKEND_PKG)" >&2; exit 1; }
	@sources=$$(find "$(BACKEND_PKG)" -name '*.py' -type f | wc -l); \
	tests=$$(find "$(BACKEND_PKG)/tests" -name 'test_*.py' -type f | wc -l); \
	test "$$sources" -gt 0 || { \
		printf 'FAIL no Python sources under %s\n' "$(BACKEND_PKG)" >&2; exit 1; }; \
	test "$$tests" -gt 0 || { \
		printf 'FAIL no test modules under %s/tests\n' "$(BACKEND_PKG)" >&2; exit 1; }; \
	printf 'BACKEND_SOURCES_PRESENT (package %s; sources %s; test modules %s)\n' \
		"$(BACKEND_PKG)" "$$sources" "$$tests"

check-frontend: ## Run deterministic frontend lint and type controls
	$(BUN) run --cwd $(APP_DIR) lint
	$(BUN) run --cwd $(APP_DIR) typecheck
	$(BUN) run --cwd $(WEB_DIR) typecheck
	$(BUN) run --cwd $(TAURI_DIR) typecheck

check: check-backend check-frontend ## Run all provider-boundary, unit, lint, and type controls
	@echo -e "$(GREEN)✓ All checks passed$(NC)"

# =============================================================================
# TESTING
# =============================================================================

.PHONY: test test-backend test-frontend

test: test-backend test-frontend ## Run all tests
	@echo -e "$(GREEN)✓ All tests passed$(NC)"

test-backend: ## Run Python backend tests (requires pytest)
	@echo -e "$(BLUE)Running backend tests...$(NC)"
	@$(MAKE) --no-print-directory assert-backend-sources
	uv run --frozen --isolated --group dev python -I -m unittest discover \
		-s $(BACKEND_PKG)/tests -t . -p 'test_*.py' -v

test-frontend: ## Run frontend tests (requires test script in package.json)
	@echo -e "$(BLUE)Running frontend tests...$(NC)"
	@if bun run test --help >/dev/null 2>&1; then \
		bun run test; \
	else \
		echo -e "$(YELLOW)No test script configured$(NC)"; \
		exit 1; \
	fi

# =============================================================================
# LOGS & DEBUGGING
# =============================================================================

.PHONY: logs docs

logs: ## Tail backend logs
	@echo -e "$(BLUE)Tailing logs (Ctrl+C to stop)...$(NC)"
	tail -f $(BACKEND_DIR)/logs/*.log 2>/dev/null || echo "No log files found"

docs: ## Open API documentation (backend must be running)
	@echo -e "$(BLUE)Opening API docs...$(NC)"
	open http://localhost:17493/docs 2>/dev/null || xdg-open http://localhost:17493/docs

# =============================================================================
# CLEAN
# =============================================================================

.PHONY: clean clean-python clean-build clean-all

clean: ## Clean build artifacts
	@echo -e "$(BLUE)Cleaning build artifacts...$(NC)"
	rm -rf $(TAURI_DIR)/src-tauri/target/release
	rm -rf $(WEB_DIR)/dist
	rm -rf $(APP_DIR)/dist
	@echo -e "$(GREEN)✓ Build artifacts cleaned$(NC)"

clean-python: ## Clean Python cache and virtual environment
	@echo -e "$(BLUE)Cleaning Python files...$(NC)"
	rm -rf $(VENV)
	find $(BACKEND_DIR) -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	find $(BACKEND_DIR) -type f -name "*.pyc" -delete 2>/dev/null || true
	@echo -e "$(GREEN)✓ Python environment cleaned$(NC)"

clean-build: ## Clean Rust/Tauri build cache
	@echo -e "$(BLUE)Cleaning Rust build cache...$(NC)"
	cd $(TAURI_DIR)/src-tauri && cargo clean
	@echo -e "$(GREEN)✓ Rust cache cleaned$(NC)"

clean-all: clean clean-python clean-build ## Nuclear clean (everything)
	@echo -e "$(BLUE)Cleaning node_modules...$(NC)"
	rm -rf node_modules
	rm -rf $(APP_DIR)/node_modules
	rm -rf $(TAURI_DIR)/node_modules
	rm -rf $(WEB_DIR)/node_modules
	@echo -e "$(GREEN)✓ Full clean complete$(NC)"
