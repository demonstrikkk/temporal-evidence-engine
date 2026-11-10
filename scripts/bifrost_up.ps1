# Start (or stop) the Bifrost gateway without Docker.
#
# Bifrost ships as a Go binary. On this machine there is no Docker, and the
# gateway is the only model path the backend is allowed to speak, so the run
# instructions cannot depend on a container runtime.
#
# The binary is resolved in this order:
#   1. $env:BIFROST_BIN                       (an explicit path you already trust)
#   2. the npm wrapper's cache               (%LOCALAPPDATA%\bifrost\<version>\bin)
#   3. a fresh download through `npx @maximhq/bifrost`
#
# Usage:
#   scripts\bifrost_up.ps1                 # start and wait until it answers
#   scripts\bifrost_up.ps1 -Port 8081      # different port
#   scripts\bifrost_up.ps1 -Reset          # wipe the config store and re-import
#   scripts\bifrost_up.ps1 -Stop           # stop the process this script started
#
# Provider keys are read by Bifrost from the environment named in
# bifrost\config.json (`env.GROQ_API_KEY`), never from this file.

[CmdletBinding()]
param(
    [int]$Port = 8080,
    [switch]$Stop,
    [switch]$Reset,
    [string]$Version = '1.6.3',
    [int]$TimeoutSeconds = 60
)

$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
$appDir = Join-Path $repo 'bifrost\.data'
$pidFile = Join-Path $appDir 'bifrost.pid'
$logFile = Join-Path $appDir 'gateway.log'
$errFile = Join-Path $appDir 'gateway.err.log'
$configSource = Join-Path $repo 'bifrost\config.json'
$virtualKey = if ($env:BIFROST_VIRTUAL_KEY) { $env:BIFROST_VIRTUAL_KEY } else { 'sk-bf-tev-local-dev' }
$baseUrl = "http://127.0.0.1:$Port"

function Stop-Gateway {
    if (-not (Test-Path $pidFile)) {
        Write-Host "No pid file at $pidFile; nothing to stop." -ForegroundColor Yellow
        return
    }
    $existing = Get-Content $pidFile | Select-Object -First 1
    if ($existing -and (Get-Process -Id $existing -ErrorAction SilentlyContinue)) {
        Write-Host "Stopping Bifrost (pid $existing)..." -ForegroundColor Cyan
        # The gateway is started under `cmd.exe`, so the recorded pid owns a tree.
        # Killing only the parent leaves a listener on the port.
        & taskkill.exe /PID $existing /T /F *> $null
        Start-Sleep -Milliseconds 500
    }
    Remove-Item $pidFile -Force -ErrorAction SilentlyContinue
}

function Resolve-Binary {
    if ($env:BIFROST_BIN) {
        if (-not (Test-Path $env:BIFROST_BIN)) {
            throw "BIFROST_BIN points at a file that does not exist: $env:BIFROST_BIN"
        }
        return $env:BIFROST_BIN
    }

    $cached = Get-ChildItem -Path (Join-Path $env:LOCALAPPDATA 'bifrost') `
        -Recurse -File -Filter 'bifrost-http*' -ErrorAction SilentlyContinue |
        Where-Object { $_.Length -gt 1MB } |
        Sort-Object LastWriteTime -Descending |
        Select-Object -First 1
    if ($cached) {
        # The npm wrapper caches the binary with a `-0` suffix; the name is not
        # meaningful, but a stable copy is easier to point at from docs.
        $stable = Join-Path $cached.DirectoryName 'bifrost-http.exe'
        if ($cached.FullName -ne $stable) {
            Copy-Item $cached.FullName $stable -Force
        }
        return $stable
    }

    Write-Host 'No cached Bifrost binary; downloading one through npm...' -ForegroundColor Cyan
    $probe = Join-Path ([System.IO.Path]::GetTempPath()) ("bifrost-probe-" + [guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Force $probe | Out-Null
    try {
        Push-Location $probe
        $env:npm_config_yes = 'true'
        & npx --yes "@maximhq/bifrost@$Version" --help 2>&1 | Out-Null
    }
    finally {
        Pop-Location
    }
    Remove-Item $probe -Recurse -Force -ErrorAction SilentlyContinue
    $cached = Get-ChildItem -Path (Join-Path $env:LOCALAPPDATA 'bifrost') `
        -Recurse -File -Filter 'bifrost-http*' -ErrorAction SilentlyContinue |
        Where-Object { $_.Length -gt 1MB } |
        Sort-Object LastWriteTime -Descending |
        Select-Object -First 1
    if (-not $cached) {
        throw 'The npm wrapper did not leave a Bifrost binary in %LOCALAPPDATA%\bifrost.'
    }
    $stable = Join-Path $cached.DirectoryName 'bifrost-http.exe'
    if ($cached.FullName -ne $stable) { Copy-Item $cached.FullName $stable -Force }
    return $stable
}

function Invoke-Probe([string]$Url, [int]$TimeoutSeconds) {
    # `Invoke-WebRequest -TimeoutSec` bounds the connect, not the read: against a
    # gateway that accepts the socket while it is still booting, that call can
    # hang for minutes. `HttpClient.Timeout` bounds the whole exchange, which is
    # the property a start-up script actually needs.
    Add-Type -AssemblyName System.Net.Http -ErrorAction SilentlyContinue
    $client = New-Object System.Net.Http.HttpClient
    $client.Timeout = [TimeSpan]::FromSeconds($TimeoutSeconds)
    $client.DefaultRequestHeaders.Add('x-bf-vk', $virtualKey)
    $client.DefaultRequestHeaders.Add('Authorization', "Bearer $virtualKey")
    try {
        $response = $client.GetAsync($Url).GetAwaiter().GetResult()
        $body = $response.Content.ReadAsStringAsync().GetAwaiter().GetResult()
        return @{ code = [int]$response.StatusCode; body = $body }
    }
    catch {
        # A refused connection has no response at all; report it as 0.
        return @{ code = 0; body = $_.Exception.Message }
    }
    finally {
        $client.Dispose()
    }
}

function Test-Gateway([int]$Seconds) {
    # Liveness is `/health`: unauthenticated, and it answers even when no provider
    # key is configured. Authentication is a separate question, asked below,
    # because a gateway can be perfectly alive and refuse every call.
    $deadline = (Get-Date).AddSeconds($Seconds)
    while ((Get-Date) -lt $deadline) {
        $probe = Invoke-Probe "$baseUrl/health" 3
        if ($probe.code -gt 0 -and $probe.code -lt 400) { return $true }
        Start-Sleep -Milliseconds 750
    }
    return $false
}

function Test-VirtualKey {
    # 2xx   the key is accepted and at least one provider answers.
    # 400   the key is accepted; no provider can answer yet (missing provider key,
    #       or the fallback host is down). The gateway is usable, the run is not.
    # 401/403 the key itself was refused: every call would fail, so say so loudly.
    $probe = Invoke-Probe "$baseUrl/v1/models" 15
    if ($probe.code -ge 200 -and $probe.code -lt 400) {
        return @{ ok = $true; detail = "models answered ($($probe.code))" }
    }
    if ($probe.code -eq 400) {
        return @{ ok = $true; detail = 'key accepted; no provider could answer yet' }
    }
    if ($probe.code -eq 401 -or $probe.code -eq 403) {
        return @{ ok = $false; detail = "virtual key refused ($($probe.code)): $($probe.body)" }
    }
    return @{ ok = $false; detail = "gateway did not answer the key probe: $($probe.body)" }
}

if ($Stop) { Stop-Gateway; return }

New-Item -ItemType Directory -Force $appDir | Out-Null

if ($Reset) {
    Stop-Gateway
    Get-ChildItem $appDir -File | Where-Object { $_.Name -ne 'config.json' } |
        Remove-Item -Force -ErrorAction SilentlyContinue
    Remove-Item (Join-Path $appDir 'config.json') -Force -ErrorAction SilentlyContinue
    Write-Host 'Config store wiped; it will be re-imported from bifrost\config.json.' -ForegroundColor Yellow
}

if (-not (Test-Path (Join-Path $appDir 'config.json'))) {
    Copy-Item $configSource (Join-Path $appDir 'config.json')
    Write-Host "Imported bifrost\config.json into $appDir" -ForegroundColor Cyan
}

if (Test-Gateway 2) {
    Write-Host "Bifrost already answers at $baseUrl." -ForegroundColor Green
    $existing = Test-VirtualKey
    if ($existing.ok) {
        Write-Host "Virtual key $virtualKey — $($existing.detail)." -ForegroundColor Green
    }
    else {
        Write-Host "Virtual key $virtualKey : $($existing.detail)" -ForegroundColor Red
        exit 1
    }
    return
}

$binary = Resolve-Binary
Write-Host "Starting Bifrost: $binary" -ForegroundColor Cyan
# The committed config resolves the Ollama base URL from `env.OLLAMA_BASE_URL`.
# Leaving it unset does not fail loudly — it produces a provider whose URL is the
# literal string "env.OLLAMA_BASE_URL" — so the default local address is filled
# in here rather than discovered later by a fallback that does not work.
if (-not $env:OLLAMA_BASE_URL) {
    $env:OLLAMA_BASE_URL = 'http://127.0.0.1:11434'
    Write-Host "OLLAMA_BASE_URL not set; using $env:OLLAMA_BASE_URL for the fallback." -ForegroundColor Yellow
}
# Started through a generated `cmd` launcher so that all three standard handles
# are resolved by the shell: stdout/stderr to files, stdin from NUL. Starting the
# binary directly leaves it holding the caller's inherited pipe, which is why a
# pipeline or CI capture never sees this script finish.
$launcher = Join-Path $appDir 'start-gateway.cmd'
$launcherBody = @"
@echo off
"$binary" -app-dir "$appDir" -host 127.0.0.1 -port $Port -log-style pretty > "$logFile" 2> "$errFile" < NUL
"@
Set-Content -Path $launcher -Value $launcherBody -Encoding ASCII
$process = Start-Process -FilePath $env:ComSpec `
    -ArgumentList @('/c', $launcher) -WindowStyle Hidden -PassThru
Set-Content -Path $pidFile -Value $process.Id

if (-not (Test-Gateway $TimeoutSeconds)) {
    Write-Host "Bifrost did not answer at $baseUrl within $TimeoutSeconds s." -ForegroundColor Red
    Write-Host "Last log lines ($logFile):" -ForegroundColor Red
    Get-Content $logFile -Tail 20 -ErrorAction SilentlyContinue
    Write-Host "Last error lines ($errFile):" -ForegroundColor Red
    Get-Content $errFile -Tail 20 -ErrorAction SilentlyContinue
    Stop-Gateway
    exit 1
}

if (-not $env:GROQ_API_KEY) {
    Write-Host 'Warning: GROQ_API_KEY is not set. Bifrost will start, but the hosted provider cannot answer.' -ForegroundColor Yellow
}

$key = Test-VirtualKey
Write-Host "Bifrost is up at $baseUrl (pid $($process.Id))." -ForegroundColor Green
if ($key.ok) {
    Write-Host "Virtual key $virtualKey : $($key.detail)." -ForegroundColor Green
}
else {
    Write-Host "Virtual key $virtualKey : $($key.detail)" -ForegroundColor Red
}
Write-Host "Logs: $logFile"
