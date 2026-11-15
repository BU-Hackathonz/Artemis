<#
  Water wallpaper prototype launcher (Windows 10/11, no install needed).

  1. Reads your current wallpaper and how Windows fits it (Fill, Fit, Stretch, Center, Tile, Span)
     from the registry, plus the desktop background colour.
  2. Copies water.html into a temp folder with a wallpaper.js next to it (image as a data: URL).
  3. Opens it in Microsoft Edge (or Chrome) app mode, fullscreen and borderless on the primary
     monitor, with a throwaway browser profile, and waits until you close it (Esc).
  4. Deletes the temp folder.

  Run:  powershell -NoProfile -ExecutionPolicy Bypass -File .\Launch-WaterWallpaper.ps1
  Or double-click Launch-WaterWallpaper.cmd
#>
[CmdletBinding()]
param()
$ErrorActionPreference = 'Stop'

$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$page = Join-Path $here 'water.html'
if (-not (Test-Path -LiteralPath $page)) { throw "water.html was not found next to this script ($here)." }

# --- 1. Wallpaper -----------------------------------------------------------------------------
$desk = Get-ItemProperty -Path 'HKCU:\Control Panel\Desktop'
$transcoded = Join-Path $env:APPDATA 'Microsoft\Windows\Themes\TranscodedWallpaper'
$wallPath = $null
if ($desk.WallPaper -and (Test-Path -LiteralPath $desk.WallPaper)) { $wallPath = $desk.WallPaper }
elseif ($desk.WallPaper -and (Test-Path -LiteralPath $transcoded)) { $wallPath = $transcoded }
elseif (-not $desk.WallPaper) { $wallPath = $null }   # solid colour desktop
elseif (Test-Path -LiteralPath $transcoded) { $wallPath = $transcoded }

$src = ''
if ($wallPath) {
  $bytes = [System.IO.File]::ReadAllBytes($wallPath)
  $mime = 'image/jpeg'
  if ($bytes.Length -ge 12) {
    if ($bytes[0] -eq 0x89 -and $bytes[1] -eq 0x50 -and $bytes[2] -eq 0x4E -and $bytes[3] -eq 0x47) { $mime = 'image/png' }
    elseif ($bytes[0] -eq 0x42 -and $bytes[1] -eq 0x4D) { $mime = 'image/bmp' }
    elseif ($bytes[0] -eq 0x52 -and $bytes[1] -eq 0x49 -and $bytes[8] -eq 0x57 -and $bytes[9] -eq 0x45) { $mime = 'image/webp' }
    elseif ($bytes[0] -eq 0x47 -and $bytes[1] -eq 0x49 -and $bytes[2] -eq 0x46) { $mime = 'image/gif' }
  }
  $src = "data:$mime;base64," + [Convert]::ToBase64String($bytes)
  Write-Host "Wallpaper: $wallPath ($mime, $([math]::Round($bytes.Length / 1MB, 1)) MB)"
} else {
  Write-Host 'Wallpaper: none (solid colour desktop)'
}

$style = switch ("$($desk.WallpaperStyle)") {
  '0'  { if ("$($desk.TileWallpaper)" -eq '1') { 'tile' } else { 'center' } }
  '2'  { 'stretch' }
  '6'  { 'fit' }
  '10' { 'fill' }
  '22' { 'span' }
  default { 'fill' }
}
$bg = 'rgb(0,0,0)'
try {
  $c = (Get-ItemProperty -Path 'HKCU:\Control Panel\Colors').Background -split '\s+'
  if ($c.Count -ge 3) { $bg = "rgb($($c[0]),$($c[1]),$($c[2]))" }
} catch { }
Write-Host "Fit: $style   Background: $bg"

# --- 2. Temp copy -----------------------------------------------------------------------------
$work = Join-Path $env:TEMP ('artemis-water-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $work | Out-Null
Copy-Item -LiteralPath $page -Destination $work
# base64 only uses A-Z a-z 0-9 + / =, so it is safe inside a JS string literal.
$js = "window.WALLPAPER = { src: '$src', style: '$style', bg: '$bg' };"
[System.IO.File]::WriteAllText((Join-Path $work 'wallpaper.js'), $js, (New-Object System.Text.UTF8Encoding($false)))

# --- 3. Browser -------------------------------------------------------------------------------
$candidates = @(
  (Join-Path ${env:ProgramFiles(x86)} 'Microsoft\Edge\Application\msedge.exe'),
  (Join-Path $env:ProgramFiles 'Microsoft\Edge\Application\msedge.exe'),
  (Join-Path $env:ProgramFiles 'Google\Chrome\Application\chrome.exe'),
  (Join-Path $env:LOCALAPPDATA 'Google\Chrome\Application\chrome.exe')
)
$browser = $candidates | Where-Object { $_ -and (Test-Path -LiteralPath $_) } | Select-Object -First 1
if (-not $browser) { Remove-Item -LiteralPath $work -Recurse -Force; throw 'Microsoft Edge or Google Chrome is required.' }

Add-Type -AssemblyName System.Windows.Forms
$screen = [System.Windows.Forms.Screen]::PrimaryScreen.Bounds
$url = ([System.Uri](Join-Path $work 'water.html')).AbsoluteUri
$userData = Join-Path $work 'profile'
# Start-Process joins arguments with spaces, so quote anything that may contain one.
$browserArgs = @(
  "--app=$url",
  "--user-data-dir=`"$userData`"",
  '--no-first-run',
  '--no-default-browser-check',
  '--disable-sync',
  "--window-position=$($screen.X),$($screen.Y)",
  '--start-fullscreen'
)

# --- 4. Run until the window closes ---------------------------------------------------------
try {
  Write-Host "Opening water preview in $(Split-Path -Leaf $browser). Press Esc in the preview to exit."
  $proc = Start-Process -FilePath $browser -ArgumentList $browserArgs -PassThru
  $proc.WaitForExit()
} finally {
  Start-Sleep -Milliseconds 800   # let the browser release its profile files
  Remove-Item -LiteralPath $work -Recurse -Force -ErrorAction SilentlyContinue
}
