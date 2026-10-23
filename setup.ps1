$ErrorActionPreference = "Stop"
$env:UV_CACHE_DIR = Join-Path $PSScriptRoot ".uv-cache"
$env:UV_PYTHON_INSTALL_DIR = Join-Path $PSScriptRoot ".uv-python"

uv python install 3.12
uv sync
Write-Host "环境准备完成。请编辑 .env 填写 DEEPSEEK_API_KEY，然后运行 .\run.ps1"
