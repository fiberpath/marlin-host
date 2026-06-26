# Python (uv) task runner
# See https://github.com/CameronBrooks11/dev-toolbox/blob/main/docs/just-conventions.md

set dotenv-load := false

# Default: show available recipes
default:
    @just --list

# Install dependencies and set up environment
setup:
    uv sync

# Format code (mutates working tree — use locally)
fmt:
    uv run ruff format .
    uv run ruff check --fix .

# Verify formatting (non-mutating — use in CI)
fmt-check:
    uv run ruff format --check .

# Run linters
lint:
    uv run ruff check .

# Type-check
typecheck:
    uv run pyright

# Format-check + lint + type-check (non-mutating — safe for CI)
check: fmt-check lint typecheck

# Run tests
test:
    uv run pytest

# Fetch the pinned Marlin source files codegen needs (into gitignored vendor/)
fetch-marlin:
    uv run python scripts/fetch_marlin.py

# Regenerate marlin_host/_constants.py from the pinned Marlin source
codegen: fetch-marlin
    uv run python scripts/gen_constants.py

# Verify the committed constants match the pinned source (CI/release drift gate)
codegen-check: codegen
    git diff --exit-code marlin_host/_constants.py

# Remove build artifacts
clean:
    rm -rf .venv dist .pytest_cache .ruff_cache
    find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
