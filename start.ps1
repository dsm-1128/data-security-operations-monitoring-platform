param([switch]$NoBrowser)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
docker compose up -d --build
if ($LASTEXITCODE -ne 0) { throw 'Docker deployment failed. Confirm Docker Desktop is running and your user can access Docker Engine.' }
$demoPort = if ($env:SECOPS_PORT) { $env:SECOPS_PORT } else { '8080' }
if (Test-Path -LiteralPath '.env') {
    $portLine = Get-Content -LiteralPath '.env' | Where-Object { $_ -match '^SECOPS_PORT=\d+$' } | Select-Object -Last 1
    if ($portLine -and -not $env:SECOPS_PORT) { $demoPort = $portLine.Split('=')[1] }
}
$demoUrl = 'http://localhost:' + $demoPort
$healthy = $false
for ($attempt = 0; $attempt -lt 30; $attempt++) {
    try { $response = Invoke-RestMethod -Uri ($demoUrl + '/healthz') -TimeoutSec 2; if ($response.status -eq 'ok') { $healthy = $true; break } } catch { }
    Start-Sleep -Seconds 1
}
if (-not $healthy) { docker compose logs --tail 50; throw 'Health check did not pass. See container logs above.' }
Write-Host ('SecOps demo is ready: ' + $demoUrl)
if (-not $NoBrowser) { Start-Process $demoUrl }
