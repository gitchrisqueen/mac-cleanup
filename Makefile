.DEFAULT_GOAL := help
SHELL := /bin/bash

.PHONY: help setup test test-all lint guards verify

help: ## Show available commands
	@grep -E '^[a-z-]+:.*?## ' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[1m%-10s\033[0m %s\n", $$1, $$2}'

setup: ## Create the venv and install dev dependencies
	@uv venv --python 3.11 && uv pip install -e '.[dev]' pytest pytest-cov pytest-timeout ruff mypy

test: ## Fast unit suite. Must stay under ten seconds.
	@.venv/bin/pytest -q -m "not slow and not realfs and not macos"

test-all: ## Full suite with coverage
	@.venv/bin/pytest -q --cov --cov-report=term-missing

lint: ## Format check, lint, typecheck
	@.venv/bin/ruff format --check . && .venv/bin/ruff check . && .venv/bin/mypy

guards: ## Structural guards: one deleter, one runner, path-free artifacts
	@./scripts/guards.sh

verify: lint guards test-all ## Everything CI runs, in order
