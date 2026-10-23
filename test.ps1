$ErrorActionPreference = "Stop"
$env:UV_CACHE_DIR = Join-Path $PSScriptRoot ".uv-cache"
$env:UV_PYTHON_INSTALL_DIR = Join-Path $PSScriptRoot ".uv-python"

uv run pytest
uv run ruff check .
uv run mypy src
