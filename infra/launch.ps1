<#
.SYNOPSIS
  Double-click launcher for the Artemis demo (use infra\Artemis.cmd). Finds the built desktop app,
  asks which agent to use, then runs start.ps1 with it. Windows PowerShell 5.1 or PowerShell 7.

.DESCRIPTION
  This is a labeled demo/dev launcher, NOT an installer. It still needs this repository checked
  out, uv installed and the dependencies synced (infra\README.md), because the Python core is
  started by start.ps1 from the repository. It does not bundle the core.

  The Anthropic API key, if you type one, is read as a hidden prompt into this process's
  environment only. The launcher never writes it to a file, a command line or a log. It is removed
  from the environment when the launcher exits, and any in-memory copies end with the process.
  Saving it in Windows Credential Manager is a separate, optional step (infra\README.md).
  Press Enter at the prompt to use the scripted stub agent instead.

.EXAMPLE
  infra\Artemis.cmd                      # double-click, or run from any shell
  powershell -ExecutionPolicy Bypass -File infra\launch.ps1 -Agent stub -NoPause
#>
[CmdletBinding()]
param(
    [string]$AppPath = "",
    [ValidateSet("ask", "stub", "claude")][string]$Agent = "ask",
    [switch]$Conversation,    # kept for old command lines: conversation mode is now ON by default in this launcher
    [switch]$NoConversation,  # turn the built-in spoken phrases and follow-ups off (start.ps1 itself defaults to off)
    [ValidateSet("whisper", "stub")][string]$Voice = "whisper",  # "stub" skips the mic and Whisper
    [switch]$Compact,  # opt-in compact status window (start.ps1 -Compact); off by default
    [switch]$ForgetKey,  # delete the key and workspace ID saved in Windows Credential Manager, then exit
    [switch]$NoPause  # for scripts and tests: never wait for Enter
)

$ErrorActionPreference = "Stop"
# Artemis.cmd made this empty file and checks for it afterwards: gone means this script ran,
# still there means PowerShell never got this far (blocked or damaged), and Artemis.cmd says so.
if ($env:ARTEMIS_LAUNCH_MARKER) {
    Remove-Item -LiteralPath $env:ARTEMIS_LAUNCH_MARKER -Force -ErrorAction SilentlyContinue
}
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

# Anything unexpected below is explained and waited on like every other failure, so a
# double-click window never closes with only a raw error in it. The finally block further down
# has already run by the time this does, so a typed key is gone from the environment.
trap {
    Write-Host ""
    Write-Host ("Unexpected error: " + $_.Exception.Message) -ForegroundColor Red
    Finish 1 "Artemis could not start: the launcher hit an unexpected error. The line above says what."
}

. (Join-Path $Here "credstore.ps1")

if ($ForgetKey) {
    $r1 = Remove-StoredSecret $ApiKeyTarget
    $r2 = Remove-StoredSecret $WorkspaceTarget
    if ($r1 -eq "failed" -or $r2 -eq "failed") {
        Finish 1 "Could not change Windows Credential Manager ($script:CredStoreError). Delete 'Artemis/AnthropicApiKey' and 'Artemis/AnthropicWorkspaceId' by hand in Credential Manager."
    }
    if ($r1 -eq "none" -and $r2 -eq "none") { Write-Host "Nothing was saved by Artemis, so nothing was removed." }
    else { Write-Host "Removed the Anthropic key and workspace ID saved by Artemis from Windows Credential Manager." }
    # From a shortcut the window would close before this could be read.
    if (-not $NoPause) { Read-Host "Press Enter to close this window" | Out-Null }
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
            Write-Host "Using the Anthropic API key saved in Windows Credential Manager. No internet? Run Artemis.cmd -Agent stub. Run Artemis.cmd -ForgetKey to remove the saved key."
        } else {
            Write-Host "Artemis demo launcher"
            Write-Host "Type your Anthropic API key (it is hidden as you type), or press Enter for the scripted stub agent."
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
            Write-Host "Using the Anthropic API key saved in Windows Credential Manager. No internet? Run Artemis.cmd -Agent stub. Run Artemis.cmd -ForgetKey to remove the saved key."
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
    if ($offerSave -and -not $NoPause -and $script:CredStoreAvailable) {
        $answer = Read-Host "Save this in Windows Credential Manager (encrypted for your Windows account) so you don't retype it? (y/N)"
        if ($answer -match "^(y|yes)$") {
            # Only a key typed just now is saved; one that came from the store already is there.
            $saved = Save-TypedSecrets ([bool]($keyFromPrompt -and $secure))
            if ($saved.Ok) { Write-Host $saved.Message } else { Write-Host $saved.Message -ForegroundColor Red }
        }
    } elseif ($offerSave -and -not $script:CredStoreAvailable) {
        Write-Host "Saving is not available on this laptop ($script:CredStoreError). You will be asked again next time."
    }

    # ----- run -----

    Write-Host "Starting Artemis (agent: $chosen). Close the Artemis window to stop everything."
    $startArgs = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", (Join-Path $Here "start.ps1"),
        "-Agent", $chosen, "-Voice", $Voice, "-AppPath", $exe)
    if (-not $NoConversation) { $startArgs += "-Conversation" }  # on by default here (contracts.md 5.4)
    if ($Compact) { $startArgs += "-Compact" }
    # start.ps1 exit codes: 0 normal close, 1 could not start, 2 the core stopped while the app ran.
    # Anything but 0 keeps the window open: a double-click user must be able to read why it stopped.
    & powershell.exe @startArgs
    $code = $LASTEXITCODE
    if ($code -eq 2) { Finish 1 "Artemis stopped because its core stopped while the app was running. The lines above say why." }
    if ($code -ne 0) { Finish $code "Artemis stopped with an error (exit code $code). The lines above say why." }
} finally {
    if ($bstr -ne [IntPtr]::Zero) { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr) }
    if ($secure) { $secure.Dispose() }
    if ($keyFromPrompt) { Remove-Item Env:\ANTHROPIC_API_KEY -ErrorAction SilentlyContinue }
    if ($workspaceFromPrompt) { Remove-Item Env:\ANTHROPIC_WORKSPACE_ID -ErrorAction SilentlyContinue }
}
