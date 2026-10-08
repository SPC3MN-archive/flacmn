# Put flac.exe, metaflac.exe and ffmpeg.exe (LGPL build) into packaging\bin for the Windows bundle.
#   powershell -ExecutionPolicy Bypass -File packaging\tools\fetch_windows.ps1
# flac: the official Xiph release. ffmpeg: BtbN's static LGPL build (no GPL components, so it can ship with the app).
$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

$FlacVersion = "1.5.0"
$FfmpegSeries = "9.0"          # BtbN keeps the newest build of each release series under the "latest" tag

$Root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$Bin = Join-Path $Root "packaging\bin"
$Work = Join-Path ([System.IO.Path]::GetTempPath()) ("iaflac-tools-" + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Force $Bin, $Work | Out-Null

function Fetch($url, $out) {
    Write-Host "fetching $url"
    Invoke-WebRequest -Uri $url -OutFile $out -UseBasicParsing
    $hash = (Get-FileHash $out -Algorithm SHA256).Hash
    Write-Host "  sha256 $hash"
    Add-Content (Join-Path $Bin "SOURCES.txt") "$url`n  sha256 $hash"
}

Set-Content (Join-Path $Bin "SOURCES.txt") "Third-party tools bundled with iaflac (see LICENSES in the release notes)`n"

# flac + metaflac
$flacZip = Join-Path $Work "flac.zip"
Fetch "https://github.com/xiph/flac/releases/download/$FlacVersion/flac-$FlacVersion-win.zip" $flacZip
Expand-Archive $flacZip -DestinationPath (Join-Path $Work "flac") -Force
$win64 = Get-ChildItem (Join-Path $Work "flac") -Recurse -Directory -Filter "Win64" | Select-Object -First 1
foreach ($exe in "flac.exe", "metaflac.exe") {
    Copy-Item (Join-Path $win64.FullName $exe) $Bin -Force
}
foreach ($dll in Get-ChildItem $win64.FullName -Filter "*.dll") { Copy-Item $dll.FullName $Bin -Force }

# ffmpeg (static, LGPL)
$release = Invoke-RestMethod "https://api.github.com/repos/BtbN/FFmpeg-Builds/releases/latest" -Headers @{ "User-Agent" = "iaflac-build" }
$asset = $release.assets | Where-Object { $_.name -eq "ffmpeg-n$FfmpegSeries-latest-win64-lgpl-$FfmpegSeries.zip" } | Select-Object -First 1
if (-not $asset) {
    $asset = $release.assets | Where-Object { $_.name -match "^ffmpeg-n[\d.]+-latest-win64-lgpl-[\d.]+\.zip$" } | Sort-Object name -Descending | Select-Object -First 1
}
if (-not $asset) { throw "no static LGPL win64 ffmpeg build found in BtbN's latest release" }
$ffZip = Join-Path $Work "ffmpeg.zip"
Fetch $asset.browser_download_url $ffZip
Expand-Archive $ffZip -DestinationPath (Join-Path $Work "ffmpeg") -Force
foreach ($exe in @("ffmpeg.exe")) {        # ffprobe is not used, and the static build is ~130 MB
    $found = Get-ChildItem (Join-Path $Work "ffmpeg") -Recurse -Filter $exe | Select-Object -First 1
    Copy-Item $found.FullName $Bin -Force
}
$lic = Get-ChildItem (Join-Path $Work "ffmpeg") -Recurse -Filter "LICENSE*" | Select-Object -First 1
if ($lic) { Copy-Item $lic.FullName (Join-Path $Bin "FFMPEG-LICENSE.txt") -Force }

& (Join-Path $Bin "flac.exe") --version
& (Join-Path $Bin "ffmpeg.exe") -hide_banner -version | Select-Object -First 1
$cfg = (& (Join-Path $Bin "ffmpeg.exe") -hide_banner -buildconf) -join " "
if ($cfg -match "--enable-gpl") { throw "this ffmpeg build is GPL; refusing to bundle it" }
Remove-Item -Recurse -Force $Work
Write-Host "tools ready in $Bin"
