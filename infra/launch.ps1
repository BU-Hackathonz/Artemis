<#
.SYNOPSIS
  Double-click launcher for the Artemis demo (use infra\Artemis.cmd). Finds the built desktop app,
  asks which agent to use, then runs start.ps1 with it. Windows PowerShell 5.1 or PowerShell 7.

.DESCRIPTION
  This is a labeled demo/dev launcher, NOT an installer. It still needs this repository checked
  out, uv installed and the dependencies synced (infra\README.md), because the Python core is
  started by start.ps1 from the repository. It does not bundle the core.

  The Anthropic API key, if you type one, is read as a hidden prompt into this process's
  environment only. It is never written to a file, a command line or a log, and it is removed when
  the launcher exits. Press Enter at the prompt to use the scripted stub agent instead.

.EXAMPLE
  infra\Artemis.cmd                      # double-click, or run from any shell
  powershell -ExecutionPolicy Bypass -File infra\launch.ps1 -Agent stub -NoPause
#>
[CmdletBinding()]
param(
    [string]$AppPath = "",
    [ValidateSet("ask", "stub", "claude")][string]$Agent = "ask",
    [switch]$Conversation,
    [switch]$NoPause  # for scripts and tests: never wait for Enter
)

$ErrorActionPreference = "Stop"
$Here = Split-Path -Parent $MyInvocation.MyCommand.Path
$Repo = Split-Path -Parent $Here

function Finish([int]$Code, [string]$Message = "") {
    if ($Message) {
        Write-Host ""
        if ($Code -ne 0) { Write-Host $Message -ForegroundColor Red } else { Write-Host $Message }
    }
    if ($Code -ne 0 -and -not $NoPause) {
        Write-Host ""
        Read-Host "Press Enter to close this window" | Out-Null
    }
    exit $Code
}

# ----- find the app -----

$candidates = @()
if ($AppPath) { $candidates += $AppPath }
else {
    if ($env:LOCALAPPDATA) { $candidates += (Join-Path $env:LOCALAPPDATA "Artemis\app\artemis-client.exe") }
    $candidates += (Join-Path $Repo "client\src-tauri\target\release\artemis-client.exe")
}
$exe = $null
foreach ($candidate in $candidates) {
    if (Test-Path -LiteralPath $candidate) { $exe = (Resolve-Path -LiteralPath $candidate).Path; break }
}
if (-not $exe) {
    Finish 1 ("Artemis could not start: the desktop app (artemis-client.exe) was not found.`n" +
        "Looked in:`n  " + ($candidates -join "`n  ") + "`n" +
        "Get it from GitHub: Actions > 'Windows exe (manual)' > Run workflow, download the " +
        "'artemis-client-windows' artifact, and save artemis-client.exe to the first folder above.")
}

# ----- choose the agent -----

$secure = $null
$bstr = [IntPtr]::Zero
$keyFromPrompt = $false
$workspaceFromPrompt = $false
try {
    $chosen = $Agent
    if ($chosen -eq "ask") {
        if ($env:ANTHROPIC_API_KEY) {
            $chosen = "claude"
            Write-Host "Using the Anthropic API key already set in this window."
        } else {
            Write-Host "Artemis demo launcher"
            Write-Host "Type your Anthropic API key (it is hidden and kept only in this window), or press Enter for the scripted stub agent."
            $secure = Read-Host -AsSecureString "Anthropic API key"
            if ($secure.Length -gt 0) {
                $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
                $env:ANTHROPIC_API_KEY = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr)
                $keyFromPrompt = $true
                $chosen = "claude"
            } else {
                $chosen = "stub"
            }
        }
    } elseif ($chosen -eq "claude" -and -not $env:ANTHROPIC_API_KEY) {
        if ($NoPause) { Finish 1 "Artemis could not start: -Agent claude needs ANTHROPIC_API_KEY in this window." }
        $secure = Read-Host -AsSecureString "Anthropic API key"
        if ($secure.Length -eq 0) { Finish 1 "Artemis could not start: -Agent claude needs an API key." }
        $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
        $env:ANTHROPIC_API_KEY = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr)
        $keyFromPrompt = $true
    }
    if ($chosen -eq "claude" -and -not $env:ANTHROPIC_WORKSPACE_ID -and -not $NoPause) {
        $ws = Read-Host "Workspace ID (only if your key needs one; Enter to skip)"
        if ($ws) { $env:ANTHROPIC_WORKSPACE_ID = $ws; $workspaceFromPrompt = $true }
    }

    # ----- run -----

    Write-Host "Starting Artemis (agent: $chosen). Close the Artemis window to stop everything."
    $startArgs = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", (Join-Path $Here "start.ps1"),
        "-Agent", $chosen, "-AppPath", $exe)
    if ($Conversation) { $startArgs += "-Conversation" }
    & powershell.exe @startArgs
    $code = $LASTEXITCODE
    if ($code -ne 0) { Finish $code "Artemis stopped with an error (exit code $code). The lines above say why." }
} finally {
    if ($bstr -ne [IntPtr]::Zero) { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr) }
    if ($secure) { $secure.Dispose() }
    if ($keyFromPrompt) { Remove-Item Env:\ANTHROPIC_API_KEY -ErrorAction SilentlyContinue }
    if ($workspaceFromPrompt) { Remove-Item Env:\ANTHROPIC_WORKSPACE_ID -ErrorAction SilentlyContinue }
}
