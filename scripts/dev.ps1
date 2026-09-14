# Dashboard dev loop: the SED API with a dev token plus the Vite dev server, whose /api proxy sends the same token.
# Usage:  powershell -ExecutionPolicy Bypass -File scripts\dev.ps1 [-SedProfile synthetic] [-Reload] [-ApiOnly]
#   default   python -m sed serve --dev --no-browser --port 8000   (same lock file and hardening as `sed serve`)
#   -Reload   python -m uvicorn --factory sed.api.serve:dev_app --reload   (restarts the API when src\ changes)
#   -ApiOnly  do not start Vite
# SED_DEV_TOKEN is generated for this session unless already set; it is never printed. Stop with Ctrl+C.
param(
    [string]$SedProfile = "synthetic",
    [switch]$Reload,
    [switch]$ApiOnly
)
$ErrorActionPreference = "Stop"
$Repo = Split-Path -Parent $PSScriptRoot
$Port = 8000  # web/vite.config.ts proxies /api to 127.0.0.1:8000
$Python = Join-Path $Repo ".venv\Scripts\python.exe"
$Web = Join-Path $Repo "web"

if (-not (Test-Path $Python)) {
    Write-Host "No virtual environment at $Python; run scripts\setup.ps1 first." -ForegroundColor Red
    exit 4
}

$env:UV_NO_SYNC = "1"
$env:PYTHONUTF8 = "1"
$env:SED_PROFILE = $SedProfile
if (-not $env:SED_DEV_TOKEN) {
    $bytes = New-Object byte[] 32
    $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    try { $rng.GetBytes($bytes) } finally { $rng.Dispose() }
    $env:SED_DEV_TOKEN = [Convert]::ToBase64String($bytes).TrimEnd("=").Replace("+", "-").Replace("/", "_")
}

# Start-Process joins arguments with spaces, so a path that contains a space is quoted explicitly.
function Format-Arg([string]$Value) {
    if ($Value -match "\s") { return '"' + $Value + '"' }
    return $Value
}

if ($Reload) {
    $apiArgs = @(
        "-m", "uvicorn", "sed.api.serve:dev_app", "--factory", "--reload",
        "--reload-dir", (Format-Arg (Join-Path $Repo "src")), "--host", "127.0.0.1", "--port", "$Port"
    )
} else {
    $apiArgs = @("-m", "sed", "serve", "--dev", "--no-browser", "--port", "$Port", "--profile", $SedProfile)
}

$dataRoot = if ($env:SED_DATA_ROOT) { $env:SED_DATA_ROOT } else { Join-Path $env:LOCALAPPDATA "sed" }
$lockFile = Join-Path (Join-Path $dataRoot $SedProfile) "serve.lock"

Write-Host "Starting the SED API on http://127.0.0.1:$Port (profile $SedProfile)" -ForegroundColor Cyan
$api = Start-Process -FilePath $Python -ArgumentList $apiArgs -WorkingDirectory $Repo -NoNewWindow -PassThru
$exitCode = 0
try {
    if ($ApiOnly -or -not (Test-Path (Join-Path $Web "package.json"))) {
        if (-not $ApiOnly) {
            Write-Host "web\package.json not found; running the API only." -ForegroundColor Yellow
        }
        Wait-Process -Id $api.Id
    } else {
        if (-not (Test-Path (Join-Path $Web "node_modules"))) {
            Write-Host "Installing web dependencies (npm ci)" -ForegroundColor Cyan
            & npm --prefix "$Web" ci
            if ($LASTEXITCODE -ne 0) { throw "npm ci failed (exit $LASTEXITCODE)" }
        }
        Write-Host "Starting Vite (the dev server prints its URL)" -ForegroundColor Cyan
        & npm --prefix "$Web" run dev
        $exitCode = $LASTEXITCODE
    }
} finally {
    if (-not $api.HasExited) {
        # /T also stops the uvicorn reload worker processes.
        & taskkill.exe /PID "$($api.Id)" /T /F | Out-Null
    }
    # A force-stopped server cannot remove its own lock; drop it once its pid is gone (restore ignores it anyway).
    if (Test-Path $lockFile) {
        $lockPid = 0
        $first = (Get-Content -Path $lockFile -TotalCount 1) -split "\s+" | Select-Object -First 1
        if ([int]::TryParse($first, [ref]$lockPid) -and -not (Get-Process -Id $lockPid -ErrorAction SilentlyContinue)) {
            Remove-Item -Path $lockFile -Force -ErrorAction SilentlyContinue
        }
    }
}
exit $exitCode
