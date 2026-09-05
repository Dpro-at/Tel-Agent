$ErrorActionPreference = "Stop"

$app = $PSScriptRoot
$current = [Version](Get-Content (Join-Path $app "version.txt") -Raw).ToString()
$release = Invoke-RestMethod -Headers @{ "User-Agent" = "Tel-Agent updater" } -Uri "https://api.github.com/repos/Dpro-at/Tel-Agent/releases/latest"
$candidate = [Version]($release.tag_name.TrimStart("v"))
if ($candidate -le [Version]$current) { exit 0 }

$asset = $release.assets | Where-Object { $_.name -match "^Tel-Agent-.+-windows-x64-unsigned\\.exe$" } | Select-Object -First 1
if (-not $asset) { exit 0 }

$installer = Join-Path $env:TEMP $asset.name
Invoke-WebRequest -Uri $asset.browser_download_url -OutFile $installer
if ($asset.digest -and $asset.digest -match "^sha256:(.+)$") {
    if ((Get-FileHash -Algorithm SHA256 $installer).Hash.ToLowerInvariant() -ne $Matches[1].ToLowerInvariant()) {
        throw "The downloaded installer did not match GitHub's SHA-256 digest."
    }
}
Start-Process -FilePath $installer -ArgumentList "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART" -Wait
