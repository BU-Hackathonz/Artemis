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
    [ValidateSet("whisper", "stub")][string]$Voice = "whisper",  # "stub" skips the mic and Whisper
    [switch]$ForgetKey,  # delete the key and workspace ID saved in Windows Credential Manager, then exit
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

. (Join-Path $Here "credstore.ps1")

if ($ForgetKey) {
    [void](Remove-StoredSecret $ApiKeyTarget)
    [void](Remove-StoredSecret $WorkspaceTarget)
    Write-Host "Removed the Anthropic key and workspace ID saved by Artemis from Windows Credential Manager (if there were any)."
    exit 0
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
    if ($AppPath) {
        Finish 1 "Artemis could not start: there is no app at the -AppPath you gave: $AppPath"
    }
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
    $savedKey = $null
    if ($chosen -ne "stub" -and -not $env:ANTHROPIC_API_KEY) { $savedKey = Get-StoredSecret $ApiKeyTarget }
    if ($chosen -eq "ask") {
        if ($env:ANTHROPIC_API_KEY) {
            $chosen = "claude"
            Write-Host "Using the Anthropic API key found in the environment (set in this window, or saved in Windows settings)."
        } elseif ($savedKey) {
            $env:ANTHROPIC_API_KEY = $savedKey
            $keyFromPrompt = $true
            $chosen = "claude"
            Write-Host "Using the Anthropic API key saved in Windows Credential Manager. Run launch.ps1 -ForgetKey to remove it."
        } else {
            Write-Host "Artemis demo launcher"
            Write-Host "Type your Anthropic API key (it is hidden and kept only in this window), or press Enter for the scripted stub agent."
            $secure = Read-Host -AsSecureString "Anthropic API key"
            if ($secure.Length -gt 0) {
                $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
                $env:ANTHROPIC_API_KEY = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr)
                $keyFromPrompt = $true
                $chosen = "claude"
                $offerSave = $true
            } else {
                $chosen = "stub"
            }
        }
    } elseif ($chosen -eq "claude" -and -not $env:ANTHROPIC_API_KEY) {
        if ($savedKey) {
            $env:ANTHROPIC_API_KEY = $savedKey
            $keyFromPrompt = $true
            Write-Host "Using the Anthropic API key saved in Windows Credential Manager. Run launch.ps1 -ForgetKey to remove it."
        } else {
            if ($NoPause) { Finish 1 "Artemis could not start: -Agent claude needs ANTHROPIC_API_KEY in this window." }
            $secure = Read-Host -AsSecureString "Anthropic API key"
            if ($secure.Length -eq 0) { Finish 1 "Artemis could not start: -Agent claude needs an API key." }
            $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
            $env:ANTHROPIC_API_KEY = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr)
            $keyFromPrompt = $true
            $offerSave = $true
        }
    }
    $savedKey = $null
    if ($chosen -eq "claude" -and -not $env:ANTHROPIC_WORKSPACE_ID) {
        $savedWs = Get-StoredSecret $WorkspaceTarget
        if ($savedWs) {
            $env:ANTHROPIC_WORKSPACE_ID = $savedWs
            $workspaceFromPrompt = $true
        } elseif (-not $NoPause) {
            $ws = Read-Host "Workspace ID (only if your key needs one; Enter to skip)"
            if ($ws) { $env:ANTHROPIC_WORKSPACE_ID = $ws; $workspaceFromPrompt = $true; $offerSave = $true }
        }
    }
    if ($offerSave -and -not $NoPause) {
        $answer = Read-Host "Save this in Windows Credential Manager (encrypted for your Windows account) so you don't retype it? (y/N)"
        if ($answer -match "^(y|yes)$") {
            if ($keyFromPrompt) { [void](Set-StoredSecret $ApiKeyTarget $env:ANTHROPIC_API_KEY) }
            if ($env:ANTHROPIC_WORKSPACE_ID) { [void](Set-StoredSecret $WorkspaceTarget $env:ANTHROPIC_WORKSPACE_ID) }
            Write-Host "Saved. Run launch.ps1 -ForgetKey to remove it."
        }
    }

    # ----- run -----

    Write-Host "Starting Artemis (agent: $chosen). Close the Artemis window to stop everything."
    $startArgs = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", (Join-Path $Here "start.ps1"),
        "-Agent", $chosen, "-Voice", $Voice, "-AppPath", $exe)
    if ($Conversation) { $startArgs += "-Conversation" }
    # start.ps1 exits 0 even when the core dies while the app runs, so watch its output for that
    # and keep the window open: a double-click user must be able to read why it stopped.
    $coreDied = $false
    & powershell.exe @startArgs | ForEach-Object {
        Write-Host $_
        if ($_ -match "The core stopped while the app was running") { $coreDied = $true }
    }
    $code = $LASTEXITCODE
    if ($code -ne 0) { Finish $code "Artemis stopped with an error (exit code $code). The lines above say why." }
    if ($coreDied) { Finish 1 "Artemis stopped because its core stopped while the app was running. The lines above say why." }
} finally {
    if ($bstr -ne [IntPtr]::Zero) { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr) }
    if ($secure) { $secure.Dispose() }
    if ($keyFromPrompt) { Remove-Item Env:\ANTHROPIC_API_KEY -ErrorAction SilentlyContinue }
    if ($workspaceFromPrompt) { Remove-Item Env:\ANTHROPIC_WORKSPACE_ID -ErrorAction SilentlyContinue }
}
