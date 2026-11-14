<#
.SYNOPSIS
  Starts Artemis: the core on a free loopback port with a fresh launch token, then the desktop app.
  Implements docs/contracts.md section 6. Windows PowerShell 5.1 or PowerShell 7.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File infra\start.ps1                  # core + npm run tauri dev
  powershell -ExecutionPolicy Bypass -File infra\start.ps1 -Agent claude    # real agent (needs ANTHROPIC_API_KEY)
  powershell -ExecutionPolicy Bypass -File infra\start.ps1 -AppPath C:\path\artemis-client.exe
  powershell -ExecutionPolicy Bypass -File infra\start.ps1 -CoreOnly        # just the core
#>
[CmdletBinding()]
param(
    [switch]$Dev,
    [string]$AppPath = "",
    [switch]$CoreOnly,
    [ValidateSet("stub", "claude")][string]$Agent = "stub",
    [ValidateSet("whisper", "stub")][string]$Voice = "whisper",
    [switch]$Conversation,  # ARTEMIS_CONVERSATION=on: built-in phrases and follow-ups (contracts.md 5.4)
    [int]$HealthTimeoutS = 120
)

$ErrorActionPreference = "Stop"
$Repo = Split-Path -Parent $PSScriptRoot
$Server = Join-Path $Repo "server"
$Client = Join-Path $Repo "client"
$LogDir = Join-Path $env:LOCALAPPDATA "Artemis\logs"
$CoreLog = Join-Path $LogDir "core.log"

function Fail([string]$Message) {
    Write-Host ""
    Write-Host "Artemis could not start: $Message" -ForegroundColor Red
    exit 1
}

function Stop-Tree($Process) {
    if ($null -ne $Process -and -not $Process.HasExited) {
        try { & taskkill.exe /T /F /PID $Process.Id 2>&1 | Out-Null } catch { }
    }
}

function Show-CoreLog {
    if (Test-Path $CoreLog) {
        Write-Host "Last lines of the core log ($CoreLog):"
        Get-Content -Path $CoreLog -Tail 20 | ForEach-Object { Write-Host "  $_" }
    }
}

function New-LaunchToken {
    # 32 random bytes, base64url without padding.
    $bytes = New-Object byte[] 32
    $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    try { $rng.GetBytes($bytes) } finally { $rng.Dispose() }
    return [Convert]::ToBase64String($bytes).TrimEnd("=").Replace("+", "-").Replace("/", "_")
}

function Get-FreePort {
    $listener = New-Object System.Net.Sockets.TcpListener([System.Net.IPAddress]::Loopback, 0)
    $listener.Start()
    try { return $listener.LocalEndpoint.Port } finally { $listener.Stop() }
}

function Start-Child([string]$FileName, [string]$Arguments, [string]$WorkingDirectory, [hashtable]$Environment, [string[]]$Withhold = @()) {
    # Secrets go in the child's environment only: never in argv (any process can list command lines).
    # $Withhold drops inherited variables a child doesn't need (the app never needs the Claude key).
    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = $FileName
    $psi.Arguments = $Arguments
    $psi.WorkingDirectory = $WorkingDirectory
    $psi.UseShellExecute = $false
    foreach ($key in $Withhold) {
        if ($psi.EnvironmentVariables.ContainsKey($key)) { $psi.EnvironmentVariables.Remove($key) }
    }
    foreach ($key in $Environment.Keys) {
        $psi.EnvironmentVariables[$key] = [string]$Environment[$key]
    }
    return [System.Diagnostics.Process]::Start($psi)
}

# Only the core talks to Claude; the desktop app never gets the key.
$coreOnlySecrets = @("ANTHROPIC_API_KEY", "ANTHROPIC_WORKSPACE_ID")

# ----- preflight -----

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    # Not on PATH: look where the uv installers and `pip install uv` put it (Yaovi's Windows run).
    $uvCandidates = @(
        (Join-Path $env:USERPROFILE ".local\bin\uv.exe"),
        (Join-Path $env:USERPROFILE ".cargo\bin\uv.exe")
    )
    if ($env:APPDATA) {
        $uvCandidates += @(Get-ChildItem -Path (Join-Path $env:APPDATA "Python\Python3*\Scripts\uv.exe") -ErrorAction SilentlyContinue | ForEach-Object { $_.FullName })
    }
    if ($env:LOCALAPPDATA) {
        $uvCandidates += @(Get-ChildItem -Path (Join-Path $env:LOCALAPPDATA "Programs\Python\Python3*\Scripts\uv.exe") -ErrorAction SilentlyContinue | ForEach-Object { $_.FullName })
    }
    $uvFound = $null
    foreach ($candidate in $uvCandidates) {
        if ($candidate -and (Test-Path -LiteralPath $candidate)) { $uvFound = $candidate; break }
    }
    if (-not $uvFound) {
        Fail "uv is not installed or not on PATH. Install it from https://docs.astral.sh/uv/ and open a new terminal."
    }
    # For this script and the processes it starts only; the user's PATH is not changed.
    $env:PATH = (Split-Path -Parent $uvFound) + ";" + $env:PATH
    Write-Host "Using uv at $uvFound (not on PATH)."
}
if ($Agent -eq "claude" -and -not $env:ANTHROPIC_API_KEY) {
    Fail "ANTHROPIC_API_KEY is not set, and -Agent claude needs it. Set it, or run with -Agent stub."
}
if (-not $CoreOnly -and -not $AppPath -and -not (Get-Command npm -ErrorAction SilentlyContinue)) {
    Fail "npm is not installed, and dev mode runs the app with npm run tauri dev. Install Node.js, or pass -AppPath."
}
if ($AppPath -and -not (Test-Path $AppPath)) {
    Fail "the app was not found at $AppPath."
}
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

$token = New-LaunchToken
$port = Get-FreePort
$url = "http://127.0.0.1:$port"

$groups = ""
if ($Voice -eq "whisper") { $groups += " --group voice" }
if ($Agent -eq "claude") { $groups += " --group agent --group security --group echo" }  # echo: inspect_page

$core = $null
$app = $null
try {
    # ----- core -----
    $conversationMode = "off"
    if ($Conversation) { $conversationMode = "on" }
    Write-Host "Starting the Artemis core on $url (agent: $Agent, voice: $Voice, conversation: $conversationMode)..."
    $coreArgs = "/d /c uv run --project `"$Server`"$groups uvicorn artemis.core.main:app --host 127.0.0.1 --port $port > `"$CoreLog`" 2>&1"
    $core = Start-Child "cmd.exe" $coreArgs $Server @{
        "ARTEMIS_TOKEN" = $token
        "ARTEMIS_AGENT" = $Agent
        "ARTEMIS_VOICE" = $Voice
        "ARTEMIS_CONVERSATION" = $conversationMode
    }

    $headers = @{ "Authorization" = "Bearer $token" }
    $ready = $false
    $deadline = (Get-Date).AddSeconds($HealthTimeoutS)
    while ((Get-Date) -lt $deadline) {
        if ($core.HasExited) { break }
        try {
            $response = Invoke-WebRequest -UseBasicParsing -Uri "$url/health" -Headers $headers -TimeoutSec 2
            if ($response.StatusCode -eq 200) { $ready = $true; break }
        } catch {
            Start-Sleep -Milliseconds 500
        }
    }
    if (-not $ready) {
        Show-CoreLog
        if ($core.HasExited) { Fail "the core exited before it answered /health." }
        Fail "the core did not answer /health within $HealthTimeoutS seconds."
    }
    Write-Host "Core is ready."

    # ----- app -----
    if ($CoreOnly) {
        Write-Host "Core only: $url (the launch token is in the core's environment and is not shown). Ctrl+C to stop."
        while (-not $core.HasExited) { Start-Sleep -Milliseconds 500 }
        Show-CoreLog
        Fail "the core stopped."
    }

    $appEnv = @{ "ARTEMIS_CORE_URL" = $url; "ARTEMIS_TOKEN" = $token }
    if ($AppPath) {
        Write-Host "Starting the app: $AppPath"
        $app = Start-Child $AppPath "" (Split-Path -Parent $AppPath) $appEnv $coreOnlySecrets
    } else {
        Write-Host "Starting the app in dev mode (npm run tauri dev). The first build takes a few minutes."
        $app = Start-Child "cmd.exe" "/d /c npm run tauri dev" $Client $appEnv $coreOnlySecrets
    }

    # Polling instead of WaitForExit, so Ctrl+C reaches the finally block.
    $coreDied = $false
    while (-not $app.HasExited) {
        if ($core.HasExited) {
            Show-CoreLog
            Write-Host "The core stopped while the app was running." -ForegroundColor Red
            $coreDied = $true
            break
        }
        Start-Sleep -Milliseconds 500
    }
    if (-not $coreDied) { Write-Host "The app closed. Stopping the core." }
} finally {
    Stop-Tree $app
    Stop-Tree $core
    $token = $null
}
# Exit codes for wrappers such as a double-click launcher: 0 = the app was closed normally,
# 1 = could not start, 2 = the core stopped while the app was running.
if ($coreDied) { exit 2 }
