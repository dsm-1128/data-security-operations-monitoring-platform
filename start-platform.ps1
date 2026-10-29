$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot

function New-PlatformSecret {
    $bytes = New-Object byte[] 32
    $generator = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    try { $generator.GetBytes($bytes) } finally { $generator.Dispose() }
    return [BitConverter]::ToString($bytes).Replace('-', '').ToLowerInvariant()
}

if (-not (Test-Path -LiteralPath '.env.platform')) {
    $configuration = [System.IO.File]::ReadAllText((Join-Path $PSScriptRoot '.env.platform.example'))
    $configuration = $configuration.Replace('replace-with-another-random-password', (New-PlatformSecret))
    $configuration = $configuration.Replace('replace-with-random-redis-password', (New-PlatformSecret))
    $configuration = $configuration.Replace('replace-with-random-password', (New-PlatformSecret))
    [System.IO.File]::WriteAllText((Join-Path $PSScriptRoot '.env.platform'), $configuration, (New-Object System.Text.UTF8Encoding($false)))
    Write-Host 'Created .env.platform with random development passwords.'
}
docker info --format '{{.ServerVersion}}'
if ($LASTEXITCODE -ne 0) { throw 'Start Docker Desktop with Linux containers before continuing.' }
docker compose --env-file .env.platform -f compose.platform.yaml config --quiet
if ($LASTEXITCODE -ne 0) { throw 'Invalid platform configuration.' }
docker compose --env-file .env.platform -f compose.platform.yaml up -d --build
if ($LASTEXITCODE -ne 0) { throw 'Platform startup failed. Check compose logs.' }
docker compose --env-file .env.platform -f compose.platform.yaml ps
Write-Host 'Default workbench: http://127.0.0.1:18081 (configured SECOPS_PLATFORM_PORT takes precedence).'
