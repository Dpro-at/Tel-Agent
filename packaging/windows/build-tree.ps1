param(
    [Parameter(Mandatory = $true)]
    [string]$Stage
)

$ErrorActionPreference = "Stop"

# Keep the runtime versions aligned with the Linux package. The installer carries
# both runtimes, so a customer never has to install Python or Node separately.
$pythonBuild = "20250818"
$pythonVersion = "3.12.11"
$nodeVersion = "22.19.0"
$root = (Resolve-Path (Join-Path $PSScriptRoot "..\\..")).Path
$stagePath = [System.IO.Path]::GetFullPath($Stage)
$app = Join-Path $stagePath "app"

Remove-Item -LiteralPath $stagePath -Recurse -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force -Path $app | Out-Null

Invoke-WebRequest -Uri "https://github.com/astral-sh/python-build-standalone/releases/download/$pythonBuild/cpython-$pythonVersion+$pythonBuild-x86_64-pc-windows-msvc-install_only_stripped.tar.gz" -OutFile "$env:TEMP\\tel-agent-python.tar.gz"
tar -xzf "$env:TEMP\\tel-agent-python.tar.gz" -C $app

Invoke-WebRequest -Uri "https://nodejs.org/dist/v$nodeVersion/node-v$nodeVersion-win-x64.zip" -OutFile "$env:TEMP\\tel-agent-node.zip"
Expand-Archive -LiteralPath "$env:TEMP\\tel-agent-node.zip" -DestinationPath "$env:TEMP\\tel-agent-node" -Force
Move-Item -LiteralPath "$env:TEMP\\tel-agent-node\\node-v$nodeVersion-win-x64" -Destination (Join-Path $app "node")

& (Join-Path $app "python\\python.exe") -m ensurepip --upgrade
& (Join-Path $app "python\\python.exe") -m pip install --no-cache-dir $root

Copy-Item -Recurse -Force (Join-Path $root "api") $app
Copy-Item -Recurse -Force (Join-Path $root "agent") $app
Copy-Item -Recurse -Force (Join-Path $root "locales") $app
Copy-Item -Recurse -Force (Join-Path $root "alembic") $app
Copy-Item -Force (Join-Path $root "alembic.ini") $app
Copy-Item -Force (Join-Path $PSScriptRoot "tel-agent.env") (Join-Path $app ".env")
Copy-Item -Force (Join-Path $PSScriptRoot "bootstrap.py") $app
Copy-Item -Force (Join-Path $PSScriptRoot "api-start.cmd") $app
Copy-Item -Force (Join-Path $PSScriptRoot "update.ps1") $app
Copy-Item -Force (Join-Path $PSScriptRoot "update.cmd") $app
Set-Content -NoNewline -Encoding ascii -Path (Join-Path $app "version.txt") $env:TEL_AGENT_VERSION

New-Item -ItemType Directory -Force -Path (Join-Path $app "web-app") | Out-Null
Copy-Item -Recurse -Force (Join-Path $root "web\\.next\\standalone\\*") (Join-Path $app "web-app")
New-Item -ItemType Directory -Force -Path (Join-Path $app "web-app\\web\\.next\\static") | Out-Null
Copy-Item -Recurse -Force (Join-Path $root "web\\.next\\static\\*") (Join-Path $app "web-app\\web\\.next\\static")
if (Test-Path (Join-Path $root "web\\public")) {
    Copy-Item -Recurse -Force (Join-Path $root "web\\public") (Join-Path $app "web-app\\web\\public")
}

Invoke-WebRequest -Uri "https://github.com/winsw/winsw/releases/download/v2.12.0/WinSW-x64.exe" -OutFile (Join-Path $app "TelAgentService.exe")
Copy-Item -Force (Join-Path $PSScriptRoot "tel-agent-service.xml") $app

Write-Output "Staged Windows installer tree at $stagePath"
