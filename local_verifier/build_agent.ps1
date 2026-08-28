$ErrorActionPreference = 'Stop'

$repo = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$python = Join-Path $repo '.venv\Scripts\python.exe'
$buildRoot = Join-Path $PSScriptRoot 'build'
$distRoot = Join-Path $buildRoot 'pyinstaller-dist'
$workRoot = Join-Path $buildRoot 'pyinstaller-work'
$staging = Join-Path $PSScriptRoot 'staging'
$release = Join-Path $PSScriptRoot 'release'

if (-not (Test-Path -LiteralPath $python)) { throw "未找到项目虚拟环境：$python" }

foreach ($path in @($buildRoot, $staging, $release)) {
    if (Test-Path -LiteralPath $path) { Remove-Item -LiteralPath $path -Recurse -Force }
    New-Item -ItemType Directory -Path $path -Force | Out-Null
}

& $python -m PyInstaller --noconfirm --clean --distpath $distRoot --workpath (Join-Path $workRoot 'agent') (Join-Path $PSScriptRoot 'agent.spec')
Copy-Item -LiteralPath (Join-Path $distRoot 'ShipAgencySeafarerAgent.exe') -Destination $staging -Force

$browserStage = Join-Path $staging 'runtime\playwright-browsers'
New-Item -ItemType Directory -Path $browserStage -Force | Out-Null
$previousBrowsersPath = $env:PLAYWRIGHT_BROWSERS_PATH
$env:PLAYWRIGHT_BROWSERS_PATH = $browserStage
& $python -m playwright install chromium
if ($LASTEXITCODE -ne 0) { throw 'Playwright Chromium 下载失败。' }
if ($null -eq $previousBrowsersPath) { Remove-Item Env:PLAYWRIGHT_BROWSERS_PATH -ErrorAction SilentlyContinue }
else { $env:PLAYWRIGHT_BROWSERS_PATH = $previousBrowsersPath }

$isccCandidates = @(
    'C:\Program Files (x86)\Inno Setup 6\ISCC.exe',
    'C:\Program Files\Inno Setup 6\ISCC.exe',
    "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe",
    "$env:LOCALAPPDATA\Inno Setup 6\ISCC.exe"
)
$iscc = $isccCandidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
if (-not $iscc) {
    Write-Warning '未找到 Inno Setup 6，已生成 local_verifier\staging。'
    Write-Output "STAGING=$staging"
    exit 0
}

if (-not $env:SHIP_AGENCY_INSTALL_PASSWORD) { throw '未设置 SHIP_AGENCY_INSTALL_PASSWORD。' }
& $iscc "/DMyPassword=$env:SHIP_AGENCY_INSTALL_PASSWORD" (Join-Path $PSScriptRoot 'SeafarerVerifierSetup.iss')
Write-Output "INSTALLER=$release\ShipAgencySeafarerAgentSetup.exe"
