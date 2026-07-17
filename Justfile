install-requirements:
    uv sync --all-extras --dev
    uv run prek install

dev:
    uv run et --help

lint:
    uv run ruff check .

static:
    uv run mypy src

test:
    uv run pytest

test-integ:
    echo "no integration tests yet"
