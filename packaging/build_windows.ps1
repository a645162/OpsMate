# 在 Windows 上打包（需要 Visual Studio Build Tools + Python 3.10+）
$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)
uv sync --extra gui --extra build
uv run python packaging/build.py --all
Write-Host "如需安装包：winget install NSIS.NSIS; makensis packaging\installer.nsi"
