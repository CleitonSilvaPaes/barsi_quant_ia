.PHONY: install test run dry-run api lint clean

install:
	pip install -e ".[dev]"

test:
	pytest tests/ -v --cov=barsi.domain --cov-report=term-missing

run:
	python -m barsi run

dry-run:
	python -m barsi run --dry-run

api:
	python -m barsi api

clean:
	rm -rf .pytest_cache .coverage htmlcov cache/ __pycache__ src/barsi/__pycache__

