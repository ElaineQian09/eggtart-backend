.PHONY: venv install test run format lint

venv:
	python3 -m venv .venv

install:
	. .venv/bin/activate; pip install -r requirements.txt

test:
	. .venv/bin/activate; python -m pytest

run:
	. .venv/bin/activate; uvicorn main:app --reload

format:
	. .venv/bin/activate; ruff format .

lint:
	. .venv/bin/activate; ruff check .
