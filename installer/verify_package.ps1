param(
    [Parameter(Mandatory = $true)]
    [string]$InstallDir
)

$ErrorActionPreference = 'Stop'
$old = @{}
foreach ($name in @('SHIP_AGENCY_ROOT','SHIP_AGENCY_DATA_DIR','SHIP_AGENCY_TEMPLATE_DIR','SHIP_AGENCY_FRONTEND_DIR','SHIP_AGENCY_NODE','SHIP_AGENCY_PORT')) {
    $old[$name] = [Environment]::GetEnvironmentVariable($name, 'Process')
}

$env:SHIP_AGENCY_ROOT = $InstallDir
$env:SHIP_AGENCY_DATA_DIR = Join-Path $InstallDir 'data'
$env:SHIP_AGENCY_TEMPLATE_DIR = Join-Path $InstallDir 'templates'
$env:SHIP_AGENCY_FRONTEND_DIR = Join-Path $InstallDir 'frontend'
$env:SHIP_AGENCY_NODE = Join-Path $InstallDir 'runtime\node\node.exe'
$env:SHIP_AGENCY_PORT = '18000'

$process = Start-Process -FilePath (Join-Path $InstallDir 'ShipAgencyServer.exe') -WorkingDirectory $InstallDir -WindowStyle Hidden -PassThru
$healthy = $false
try {
    for ($i = 0; $i -lt 60; $i++) {
        Start-Sleep -Milliseconds 500
        try {
            $response = Invoke-WebRequest -Uri 'http://127.0.0.1:18000/api/health' -UseBasicParsing -TimeoutSec 1
            if ($response.StatusCode -eq 200) {
                $healthy = $true
                break
            }
        } catch {}
    }
    if (-not $healthy) {
        throw 'Packaged service did not start within 30 seconds.'
    }
    $homeHtml = (Invoke-WebRequest -Uri 'http://127.0.0.1:18000/' -UseBasicParsing -TimeoutSec 5).Content
    Write-Output ('PACKAGED_HEALTH=' + $response.Content)
    Write-Output ('HOME_HAS_TITLE=' + ($homeHtml.Contains('船代业务表单系统')))
} finally {
    if ($process -and -not $process.HasExited) {
        Stop-Process -Id $process.Id -Force
    }
    foreach ($name in $old.Keys) {
        [Environment]::SetEnvironmentVariable($name, $old[$name], 'Process')
    }
}
