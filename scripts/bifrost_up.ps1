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
# Exit 0 only when the gateway answers, the virtual key is accepted AND every
# model named in a routing rule's `fallbacks` is actually served. That last check
# exists because a fallback host that is down when the gateway boots disappears
# from the served model list without any error; start the fallback host first.
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

function Import-DotEnvKey([string]$Name) {
    # Bifrost reads provider keys from its own environment (`env.GROQ_API_KEY` in
    # bifrost\config.json). Nothing loads `.env` into a PowerShell session, so a
    # plain `scripts\bifrost_up.ps1` starts a gateway whose primary provider has
    # no key: it answers `/health`, accepts the virtual key, and fails every real
    # call. Read the one key from the same file the backend uses rather than
    # making "it works if you exported the variable first" part of the product.
    if (Test-Path "env:$Name") { return $false }
    $file = Join-Path $repo '.env'
    if (-not (Test-Path $file)) { return $false }
    $match = Select-String -Path $file -Pattern "^$Name=(.*)$" |
        Select-Object -First 1
    if (-not $match) { return $false }
    $value = $match.Matches[0].Groups[1].Value.Trim().Trim('"').Trim("'")
    if (-not $value) { return $false }
    Set-Item -Path "env:$Name" -Value $value
    return $true
}

function Test-ConfigDrift {
    # The gateway imports `bifrost\config.json` once, into its own store. Editing
    # the committed file afterwards changes nothing, and nothing says so: a
    # fallback can be "configured" in the file and absent from the running
    # gateway. Report the drift, and the one command that fixes it, every time
    # the script runs - including when the gateway is already up.
    $stored = Join-Path $appDir 'config.json'
    if (-not (Test-Path $stored)) { return $null }
    $a = Get-FileHash $configSource -Algorithm SHA256
    $b = Get-FileHash $stored -Algorithm SHA256
    if ($a.Hash -eq $b.Hash) { return $null }
    return ("bifrost\config.json differs from the copy the gateway imported. " +
            "The running gateway still uses the imported one. Run this script " +
            "with -Reset to import the current file (it stops the gateway).")
}

function Test-FallbackRoute {
    # A virtual key's wildcard is expanded against the provider catalogue when the
    # gateway boots. Start the gateway while the fallback host is down and the
    # fallback model is simply absent from `/v1/models`; the config still looks
    # right, and every primary rate limit then fails the call instead of
    # degrading. Measured here: 11 models served with Ollama down, 13 with it up,
    # same config. So ask the gateway what it will actually serve, rather than
    # reading the config and believing it.
    #
    # NOTE: this is an *optional, warn-only* check. Ollama is a documented
    # fallback for operators whose hardware can run it; this laptop cannot, so a
    # missing fallback model must not stop the script. A Groq rate limit with no
    # fallback produces a `partial` run — honest, not silent.
    $wanted = @()
    try {
        $config = Get-Content $configSource -Raw | ConvertFrom-Json
        foreach ($rule in @($config.governance.routing_rules)) {
            foreach ($entry in @($rule.fallbacks)) { if ($entry) { $wanted += $entry } }
        }
    }
    catch {
        return @{ ok = $false; detail = "could not read $configSource : $_" }
    }
    if (-not $wanted.Count) {
        return @{ ok = $true; detail = 'no fallback configured (a primary failure fails the run)' }
    }
    $probe = Invoke-Probe "$baseUrl/v1/models" 15
    if ($probe.code -ne 200) {
        return @{ ok = $false; wanted = $wanted
                  detail = "cannot list models ($($probe.code)): $($probe.body)" }
    }
    $served = @((($probe.body | ConvertFrom-Json).data) | ForEach-Object { $_.id })
    $missing = @($wanted | Where-Object { $served -notcontains $_ })
    if ($missing.Count) {
        return @{ ok = $false; wanted = $wanted; missing = $missing
                  detail = ("$($missing -join ', ') is configured as a fallback but " +
                            "the gateway will not serve it ($($served.Count) models " +
                            "listed). The fallback host (Ollama) was probably down " +
                            "when the gateway started. Optional fallback — " +
                            "continuing without it.") }
    }
    return @{ ok = $true; wanted = $wanted
              detail = "fallback served: $($wanted -join ', ')" }
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

$drift = Test-ConfigDrift
if ($drift) { Write-Host $drift -ForegroundColor Yellow }

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
    $route = Test-FallbackRoute
    if ($route.ok) {
        Write-Host "$($route.detail)." -ForegroundColor Green
    }
    else {
        Write-Host "Fallback: $($route.detail)" -ForegroundColor Yellow
    }
    return
}

if (-not $env:GROQ_API_KEY) {
    $imported = Import-DotEnvKey 'GROQ_API_KEY'
    if ($imported) {
        Write-Host 'Loaded GROQ_API_KEY from .env into the shell environment.' -ForegroundColor Cyan
    }
    else {
        Write-Host 'Warning: GROQ_API_KEY is not set. Bifrost will start, but the hosted provider cannot answer.' -ForegroundColor Yellow
    }
}

# Mistral is the second hosted provider, and an optional one: `bifrost\config.json`
# declares it, but a machine with no key still starts a gateway that serves Groq.
# The Vibe CLI keeps its Mistral key in the OS credential store, not in `.env`;
# copying it into `.env` once is what makes both the script and the backend agree
# on where provider keys live.
if (-not $env:MISTRAL_API_KEY) {
    $imported = Import-DotEnvKey 'MISTRAL_API_KEY'
    if ($imported) {
        Write-Host 'Loaded MISTRAL_API_KEY from .env into the shell environment.' -ForegroundColor Cyan
    }
    else {
        Write-Host 'Mistral: no MISTRAL_API_KEY, so only the providers that do have keys will answer.' -ForegroundColor Yellow
    }
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
# The gateway answering is not the same as the gateway serving what the config
# promises. Ask it; report, but do not fail — the Ollama fallback is optional
# on machines that cannot run it (this one cannot).
$route = Test-FallbackRoute
if ($route.ok) {
    Write-Host "$($route.detail)." -ForegroundColor Green
}
else {
    Write-Host "Fallback: $($route.detail)" -ForegroundColor Yellow
}
Write-Host "Logs: $logFile"
