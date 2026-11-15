<#
.SYNOPSIS
  Prepares the demo projects in %USERPROFILE%\ArtemisProjects (docs/demo.md).
  Windows PowerShell 5.1 or PowerShell 7.

.DESCRIPTION
  Copies the fixture projects under the names the demo flows open:
    calc         fixture\injected-readme  (prompt injection in a README)
    old-app      fixture\risky-run        (shell=True already on disk: the run is blocked)
    signup-page  fixture\echo-page\before.html as index.html (Echo Check; after.html stays out)
  and, after asking, removes an earlier hello-world project so the Hello World demo starts clean,
  and forgets the answer style conversation mode remembers between launches ("keep it brief" said
  in a rehearsal must not carry into the stage run).

  Safe to run again: a project that already matches the fixture is left alone. A project that
  differs (for example signup-page after the agent fixed it) is reset only after you answer y.
  Outside the projects folder only the remembered answer style file is deleted, and only when it
  looks like one. Links inside the projects folder are removed without following them.

  The projects folder is ARTEMIS_PROJECTS_DIR if set (the core reads the same variable),
  otherwise %USERPROFILE%\ArtemisProjects. The answer style file is ARTEMIS_PREFS_FILE if set,
  otherwise %USERPROFILE%\.artemis\preferences.json.

.PARAMETER Force
  Reset the demo projects, remove hello-world and forget the remembered answer style without asking.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File infra\setup-demo.ps1
  powershell -ExecutionPolicy Bypass -File infra\setup-demo.ps1 -Force
#>
[CmdletBinding()]
param(
    [switch]$Force
)

$ErrorActionPreference = "Stop"
$Repo = Split-Path -Parent $PSScriptRoot
$Fixture = Join-Path $Repo "fixture"

# Target folder name -> (file inside the project -> fixture file, relative to the repo).
$Projects = [ordered]@{
    "calc" = [ordered]@{
        "README.md" = "fixture\injected-readme\README.md"
        "main.py"   = "fixture\injected-readme\main.py"
    }
    "old-app" = [ordered]@{
        "main.py" = "fixture\risky-run\main.py"
    }
    "signup-page" = [ordered]@{
        "index.html" = "fixture\echo-page\before.html"
    }
}
$CleanStart = "hello-world"

function Fail([string]$Message) {
    Write-Host ""
    Write-Host "Demo setup failed: $Message" -ForegroundColor Red
    exit 1
}

function Confirm-Step([string]$Question) {
    if ($Force) { return $true }
    $answer = Read-Host "$Question (y/N)"
    return ($answer -match '^\s*(y|yes)\s*$')
}

function Get-ProjectPath([string]$Root, [string]$Name) {
    # Only a plain folder name directly under the projects folder.
    if ([string]::IsNullOrWhiteSpace($Name) -or $Name -match '[\\/:]' -or $Name -match '^\.+$') {
        Fail "refusing to use the project name '$Name'."
    }
    $full = [System.IO.Path]::GetFullPath((Join-Path $Root $Name))
    $parent = [System.IO.Path]::GetFullPath((Split-Path -Parent $full)).TrimEnd('\', '/')
    if ($parent -ne $Root.TrimEnd('\', '/')) {
        Fail "$full is not directly inside $Root."
    }
    return $full
}

function Test-IsLink($Item) {
    return ([int]($Item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0)
}

function Remove-FolderContents([string]$Path) {
    # Windows PowerShell 5.1's Remove-Item -Recurse can follow junctions and symbolic links and
    # delete what they point to. This walks the tree itself and removes a link, never its target.
    foreach ($child in @(Get-ChildItem -LiteralPath $Path -Force)) {
        if (Test-IsLink $child) {
            if ($child.PSIsContainer) {
                [System.IO.Directory]::Delete($child.FullName, $false)
            } else {
                [System.IO.File]::Delete($child.FullName)
            }
        } elseif ($child.PSIsContainer) {
            Remove-FolderContents $child.FullName
            [System.IO.Directory]::Delete($child.FullName, $false)
        } else {
            # Git marks its object files read-only.
            $child.Attributes = [System.IO.FileAttributes]::Normal
            [System.IO.File]::Delete($child.FullName)
        }
    }
}

function Remove-Project([string]$Root, [string]$Name) {
    $path = Get-ProjectPath $Root $Name
    if (-not (Test-Path -LiteralPath $path)) { return }
    $item = Get-Item -LiteralPath $path -Force
    if (Test-IsLink $item) {
        # The project folder itself is a link: remove the link only.
        [System.IO.Directory]::Delete($path, $false)
        return
    }
    if (-not $item.PSIsContainer) {
        Fail "$path is a file, not a project folder. Move it away and run this again."
    }
    Remove-FolderContents $path
    [System.IO.Directory]::Delete($path, $false)
}

function Test-ProjectMatches([string]$Path, $Files) {
    # True when the folder holds exactly the fixture files, byte for byte.
    if (-not (Test-Path -LiteralPath $Path -PathType Container)) { return $false }
    $present = @(Get-ChildItem -LiteralPath $Path -Force -Recurse | Where-Object { -not $_.PSIsContainer })
    if ($present.Count -ne $Files.Count) { return $false }
    foreach ($relative in $Files.Keys) {
        $target = Join-Path $Path $relative
        if (-not (Test-Path -LiteralPath $target -PathType Leaf)) { return $false }
        $source = Join-Path $Repo $Files[$relative]
        $a = (Get-FileHash -LiteralPath $source -Algorithm SHA256).Hash
        $b = (Get-FileHash -LiteralPath $target -Algorithm SHA256).Hash
        if ($a -ne $b) { return $false }
    }
    return $true
}

function Test-IsPrefsFile([string]$Path) {
    # Only a small JSON file shaped like the one server\artemis\conversation\prefs.py writes, so a
    # mistyped ARTEMIS_PREFS_FILE can never delete anything else.
    if ((Get-Item -LiteralPath $Path -Force).Length -gt 4096) { return $false }
    try {
        $data = Get-Content -LiteralPath $Path -Raw -Encoding UTF8 | ConvertFrom-Json
    } catch {
        return $false
    }
    if ($null -eq $data) { return $false }
    $names = @($data.PSObject.Properties.Name)
    return (($names -contains "version") -and ($names -contains "style"))
}

function Copy-Project([string]$Path, $Files) {
    New-Item -ItemType Directory -Force -Path $Path | Out-Null
    foreach ($relative in $Files.Keys) {
        $source = Join-Path $Repo $Files[$relative]
        $target = Join-Path $Path $relative
        Copy-Item -LiteralPath $source -Destination $target -Force
    }
}

# ----- preflight -----

if (-not (Test-Path -LiteralPath $Fixture -PathType Container)) {
    Fail "the fixture folder was not found at $Fixture. Run this script from the Artemis repository."
}
foreach ($name in $Projects.Keys) {
    foreach ($source in $Projects[$name].Values) {
        if (-not (Test-Path -LiteralPath (Join-Path $Repo $source) -PathType Leaf)) {
            Fail "the fixture file $source is missing. Update your checkout."
        }
    }
}

if ($env:ARTEMIS_PROJECTS_DIR) {
    $Root = $env:ARTEMIS_PROJECTS_DIR
} else {
    $Root = Join-Path $env:USERPROFILE "ArtemisProjects"
}
$Root = [System.IO.Path]::GetFullPath($Root).TrimEnd('\', '/')
Write-Host "Demo projects folder: $Root"
if (-not (Test-Path -LiteralPath $Root -PathType Container)) {
    New-Item -ItemType Directory -Force -Path $Root | Out-Null
    Write-Host "  Created the folder."
}

# ----- fixture projects -----

$ready = @()
$skipped = @()
foreach ($name in $Projects.Keys) {
    $files = $Projects[$name]
    $path = Get-ProjectPath $Root $name
    if (Test-ProjectMatches $path $files) {
        Write-Host "  $name is already set up."
        $ready += $name
        continue
    }
    if (Test-Path -LiteralPath $path) {
        Write-Host "  $name exists but differs from the fixture (changed in an earlier run, or extra files)."
        if (-not (Confirm-Step "  Reset $name to the fixture? This deletes $path and copies it again")) {
            Write-Host "  Left $name as it is. The demo may not go as scripted." -ForegroundColor Yellow
            $skipped += $name
            continue
        }
        Remove-Project $Root $name
    }
    Copy-Project $path $files
    Write-Host "  $name copied from the fixture." -ForegroundColor Green
    $ready += $name
}

# ----- clean start for Hello World -----

$helloPath = Get-ProjectPath $Root $CleanStart
if (Test-Path -LiteralPath $helloPath) {
    Write-Host "  $CleanStart exists from an earlier run. The Hello World demo should create it fresh."
    if (Confirm-Step "  Delete $helloPath") {
        Remove-Project $Root $CleanStart
        Write-Host "  $CleanStart removed." -ForegroundColor Green
    } else {
        Write-Host "  Kept $CleanStart. Artemis will name the new project hello-world-2 instead." -ForegroundColor Yellow
    }
} else {
    Write-Host "  $CleanStart is absent, so the Hello World demo starts clean."
}

# Leftovers from rehearsals are only listed, never deleted.
$known = @($Projects.Keys) + @($CleanStart)
$others = @(Get-ChildItem -LiteralPath $Root -Force | Where-Object { $_.PSIsContainer -and ($known -notcontains $_.Name) } | ForEach-Object { $_.Name })
if ($others.Count -gt 0) {
    Write-Host "  Other projects, left alone: $($others -join ', ')"
}

# ----- remembered answer style (conversation mode, contracts.md 5.4) -----

if ($env:ARTEMIS_PREFS_FILE) {
    $PrefsFile = $env:ARTEMIS_PREFS_FILE
    if ($PrefsFile -match '^~[\\/]') { $PrefsFile = Join-Path $env:USERPROFILE $PrefsFile.Substring(2) }
} else {
    $PrefsFile = Join-Path $env:USERPROFILE ".artemis\preferences.json"
}
if (-not [System.IO.Path]::IsPathRooted($PrefsFile)) {
    # The core ignores a relative ARTEMIS_PREFS_FILE too, so nothing is remembered there.
    Write-Host "  ARTEMIS_PREFS_FILE is not a full path, so no answer style is remembered." -ForegroundColor Yellow
} elseif (Test-Path -LiteralPath $PrefsFile -PathType Leaf) {
    if (-not (Test-IsPrefsFile $PrefsFile)) {
        Write-Host "  $PrefsFile doesn't look like an Artemis preferences file. Left it alone." -ForegroundColor Yellow
    } elseif (Confirm-Step "  Forget the remembered answer style (for example `"keep it brief`" from a rehearsal)? This deletes $PrefsFile") {
        try {
            [System.IO.File]::Delete($PrefsFile)
        } catch {
            Fail "could not delete $PrefsFile. Close Artemis and run this again."
        }
        Write-Host "  Remembered answer style forgotten: answers start at normal." -ForegroundColor Green
    } else {
        Write-Host "  Kept the remembered answer style. Saying `"reset my preferences`" also clears it." -ForegroundColor Yellow
        $skipped += "the remembered answer style"
    }
} else {
    Write-Host "  No remembered answer style, so answers start at normal."
}

Write-Host ""
if ($skipped.Count -gt 0) {
    Write-Host "Done, but not reset: $($skipped -join ', '). Run again and answer y, or use -Force." -ForegroundColor Yellow
} else {
    Write-Host "Demo projects ready: $($ready -join ', ')." -ForegroundColor Green
}
Write-Host "Next: powershell -ExecutionPolicy Bypass -File infra\start.ps1 -Agent claude (see docs/demo.md)."
