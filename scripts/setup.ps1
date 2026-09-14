# One-time developer setup (thin wrapper; all logic lives in the Python CLI).
# Usage:  powershell -ExecutionPolicy Bypass -File scripts\setup.ps1 [-SedProfile synthetic]
param(
    [string]$SedProfile = "synthetic"
)
$ErrorActionPreference = "Stop"
Set-Location -Path (Split-Path -Parent $PSScriptRoot)

function Invoke-Step([string]$Name, [scriptblock]$Block) {
    & $Block
    if ($LASTEXITCODE -ne 0) {
        Write-Host "$Name failed (exit $LASTEXITCODE)" -ForegroundColor Red
        exit $LASTEXITCODE
    }
}

Invoke-Step "uv sync" { uv sync }

$env:UV_NO_SYNC = "1"
$env:PYTHONUTF8 = "1"

$dataDir = Join-Path $env:LOCALAPPDATA "sed\$SedProfile"
$saltFile = Join-Path $dataDir "secret\pii_salt.txt"
if (Test-Path $saltFile) {
    Invoke-Step "sed init" { uv run sed init --profile $SedProfile }
} else {
    Invoke-Step "sed init --new-salt" { uv run sed init --profile $SedProfile --new-salt }
    Write-Host "Back up $saltFile now." -ForegroundColor Yellow
}

if (Test-Path ".git") {
    Invoke-Step "pre-commit install" { uv run pre-commit install }
    $email = git config --local --get user.email
    if (-not $email) {
        Write-Host "Set a repo-local git identity before the first commit: git config --local user.email <address>" -ForegroundColor Yellow
    }
}

uv run sed doctor --profile $SedProfile
exit $LASTEXITCODE
