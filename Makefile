# Makefile for local development and package checks.

PYTHON ?= python

.PHONY: help install cli-help test compile check skill-sync skill-check clean

.DEFAULT_GOAL := help

help: ## Show available commands
	@awk 'BEGIN {FS = ":.*##"; printf "\nUsage:\n  make \033[36m<target>\033[0m\n\nTargets:\n"} /^[a-zA-Z_-]+:.*##/ { printf "  \033[36m%-15s\033[0m %s\n", $$1, $$2 }' $(MAKEFILE_LIST)

install: ## Install the package in editable mode
	$(PYTHON) -m pip install -e .

cli-help: ## Run the CLI help without requiring installation
	PYTHONPATH=src $(PYTHON) -m btbkt --help

test: ## Run the pytest suite
	pytest -q

compile: ## Compile source and tests to catch syntax/import issues
	$(PYTHON) -m compileall -q src tests scripts

check: test compile ## Run the standard verification checks

skill-sync: ## Install the canonical agent workflow skill
	$(PYTHON) scripts/sync_skill.py

skill-check: ## Check the installed skill against the canonical source
	$(PYTHON) scripts/sync_skill.py --check

clean: ## Remove local build and test artifacts
	rm -rf build/ dist/ *.egg-info src/*.egg-info .pytest_cache/
	find src tests scripts -type d -name '__pycache__' -prune -exec rm -rf {} +

# 执行构建 (生成 sdist 和 wheel)
# 构建前会自动清理并重新生成 proto 代码
build: test clean
	$(PYTHON) -m build

# 上传到仓库
upload: build ## 上传到仓库
	twine upload -r cuizi7 dist/*
