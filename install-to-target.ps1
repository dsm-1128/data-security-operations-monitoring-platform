param([string]$Destination = 'D:\PythonProject\Agent_yizhiyun', [switch]$NoStart)
$ErrorActionPreference = 'Stop'
$sourcePath = [IO.Path]::GetFullPath($PSScriptRoot).TrimEnd('\')
$targetPath = [IO.Path]::GetFullPath($Destination).TrimEnd('\')
if ($sourcePath -eq $targetPath) {
    Write-Host 'Already in the target directory.'
} else {
    if ($targetPath.StartsWith($sourcePath + '\', [StringComparison]::OrdinalIgnoreCase)) { throw 'Destination must not be inside the source directory.' }
    if (Test-Path -LiteralPath $targetPath) {
        $existingFiles = Get-ChildItem -LiteralPath $targetPath -Force
        if ($existingFiles) { throw 'Destination is not empty. Choose an empty destination to avoid overwriting existing work.' }
    } else { New-Item -ItemType Directory -Path $targetPath -Force | Out-Null }
    Get-ChildItem -LiteralPath $sourcePath -Force | Where-Object { $_.Name -notin @('data','.git','.pytest_cache','__pycache__') -and $_.Extension -notin @('.zip','.log') } | ForEach-Object {
        Copy-Item -LiteralPath $_.FullName -Destination $targetPath -Recurse
    }
    Write-Host ('Project copied to: ' + $targetPath)
}
if (-not $NoStart) { & (Join-Path $targetPath 'start.ps1') }
