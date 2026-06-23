# OkLine developer tasks.  Run `make help` for the list.
.PHONY: help install lint format typecheck test check build clean

help:  ## show this help
	@grep -E '^[a-z-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "  %-12s %s\n",$$1,$$2}'

install:  ## editable install with all dev tools + the qr extra
	python -m pip install -e ".[dev,qr]"
	pre-commit install

lint:  ## ruff lint (auto-fix)
	python -m ruff check . --fix

format:  ## ruff format
	python -m ruff format .

typecheck:  ## mypy
	python -m mypy

test:  ## run the test suite
	python -m pytest

check: ## everything CI would run (lint + format-check + types + tests)
	python -m ruff check .
	python -m ruff format --check .
	python -m mypy
	python -m pytest

build:  ## build sdist + wheel
	python -m build

clean:  ## remove build artifacts
	rm -rf dist build *.egg-info .mypy_cache .ruff_cache .pytest_cache
