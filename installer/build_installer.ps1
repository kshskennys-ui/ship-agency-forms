param(
    [switch]$SkipCompile
)

$ErrorActionPreference = 'Stop'

$repo = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$python = Join-Path $repo '.venv\Scripts\python.exe'
$buildRoot = Join-Path $PSScriptRoot 'build'
$distRoot = Join-Path $buildRoot 'pyinstaller-dist'
$workRoot = Join-Path $buildRoot 'pyinstaller-work'
$staging = Join-Path $PSScriptRoot 'staging'
$release = Join-Path $PSScriptRoot 'release'

if (-not $env:SHIP_AGENCY_INSTALL_PASSWORD) {
    throw '未设置安装包密码，请先设置环境变量 SHIP_AGENCY_INSTALL_PASSWORD。'
}

if (-not (Test-Path -LiteralPath $python)) {
    throw "未找到项目虚拟环境：$python"
}

foreach ($path in @($staging, $release)) {
    if (Test-Path -LiteralPath $path) {
        Remove-Item -LiteralPath $path -Recurse -Force
    }
    New-Item -ItemType Directory -Path $path -Force | Out-Null
}

if (-not $SkipCompile) {
    if (Test-Path -LiteralPath $buildRoot) {
        Remove-Item -LiteralPath $buildRoot -Recurse -Force
    }
    New-Item -ItemType Directory -Path $buildRoot -Force | Out-Null
    & $python -m PyInstaller --noconfirm --clean --distpath $distRoot --workpath (Join-Path $workRoot 'server') $PSScriptRoot\server.spec
    & $python -m PyInstaller --noconfirm --clean --distpath $distRoot --workpath (Join-Path $workRoot 'launcher') $PSScriptRoot\launcher.spec
}

Copy-Item -LiteralPath (Join-Path $distRoot 'ShipAgencyServer.exe') -Destination $staging -Force
Copy-Item -LiteralPath (Join-Path $distRoot 'ShipAgencyLauncher.exe') -Destination $staging -Force
Copy-Item -LiteralPath (Join-Path $repo 'frontend') -Destination (Join-Path $staging 'frontend') -Recurse -Force
$templateStage = Join-Path $staging 'templates'
New-Item -ItemType Directory -Path $templateStage -Force | Out-Null
Copy-Item -Path @(
    (Join-Path $repo 'templates\*.xlsx'),
    (Join-Path $repo 'templates\*.docx')
) -Destination $templateStage -Force
$exporterStage = Join-Path $staging 'runtime\exporters'
New-Item -ItemType Directory -Path $exporterStage -Force | Out-Null
Copy-Item -Path (Join-Path $repo 'backend\app\services\*.mjs') -Destination $exporterStage -Force
$playwrightStage = Join-Path $staging 'runtime\playwright-browsers'
New-Item -ItemType Directory -Path $playwrightStage -Force | Out-Null
$previousBrowsersPath = $env:PLAYWRIGHT_BROWSERS_PATH
$env:PLAYWRIGHT_BROWSERS_PATH = $playwrightStage
& $python -m playwright install chromium
if ($LASTEXITCODE -ne 0) {
    throw 'Playwright Chromium 下载失败，无法打包海员证查询功能。'
}
if ($null -eq $previousBrowsersPath) {
    Remove-Item Env:PLAYWRIGHT_BROWSERS_PATH -ErrorAction SilentlyContinue
} else {
    $env:PLAYWRIGHT_BROWSERS_PATH = $previousBrowsersPath
}
$nodeStage = Join-Path $staging 'runtime\node'
New-Item -ItemType Directory -Path $nodeStage -Force | Out-Null
New-Item -ItemType Directory -Path (Join-Path $staging 'data') -Force | Out-Null

$nodeRoot = $env:SHIP_AGENCY_NODE_ROOT
if (-not $nodeRoot) {
    $nodeRoot = 'C:\Users\UA\.cache\codex-runtimes\codex-primary-runtime\dependencies\node'
}
$nodeExe = Join-Path $nodeRoot 'bin\node.exe'
if (-not (Test-Path -LiteralPath $nodeExe)) {
    $nodeCommand = Get-Command node -ErrorAction SilentlyContinue
    if ($nodeCommand) {
        $nodeExe = $nodeCommand.Source
        $nodeRoot = Split-Path (Split-Path $nodeExe -Parent) -Parent
    } else {
        throw '未找到 Node.js 运行时，无法打包表单导出功能。'
    }
}
Copy-Item -LiteralPath $nodeExe -Destination (Join-Path $nodeStage 'node.exe') -Force
$nodeModuleStage = Join-Path $staging 'node_modules'
New-Item -ItemType Directory -Path $nodeModuleStage -Force | Out-Null
# JSZip is a CommonJS package and its runtime dependencies are not bundled into
# jszip itself. Keep this explicit list so the installed app works on a clean
# computer without relying on a developer machine's node_modules directory.
$nodePackages = @(
    'jszip',
    'lie',
    'pako',
    'readable-stream',
    'setimmediate',
    'immediate',
    'core-util-is',
    'inherits',
    'isarray',
    'process-nextick-args',
    'safe-buffer',
    'string_decoder',
    'util-deprecate',
    '@oai'
)
foreach ($package in $nodePackages) {
    $sourcePackage = Join-Path (Join-Path $repo 'node_modules') $package
    if (-not (Test-Path -LiteralPath $sourcePackage)) {
        throw "缺少 Node.js 导出依赖：$sourcePackage"
    }
    Copy-Item -LiteralPath $sourcePackage -Destination (Join-Path $nodeModuleStage $package) -Recurse -Force
}

$isccCandidates = @(
    'C:\Program Files (x86)\Inno Setup 6\ISCC.exe',
    'C:\Program Files\Inno Setup 6\ISCC.exe',
    "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe",
    "$env:LOCALAPPDATA\Inno Setup 6\ISCC.exe"
)
$iscc = $isccCandidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
if (-not $iscc) {
    Write-Warning '未找到 Inno Setup 6。已生成 installer\staging，可安装 Inno Setup 后重新运行本脚本。'
    Write-Output "STAGING=$staging"
    exit 0
}

& $iscc "/DMyPassword=$env:SHIP_AGENCY_INSTALL_PASSWORD" $PSScriptRoot\ShipAgencySetup.iss
Write-Output "INSTALLER=$release\ShipAgencySetup.exe"
