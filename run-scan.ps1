param(
    [string]$NasRoot = $env:NAS_ROOT,
    [string]$Categories = 'parts,abnormal',
    [switch]$SkipImageDecode,
    [switch]$SyncProject
)
$ErrorActionPreference = 'Stop'
if ([string]::IsNullOrWhiteSpace($NasRoot)) {
    throw 'NAS_ROOT is required. Example: .\run-scan.ps1 -NasRoot "\\TRUENAS\exports\dataset"'
}
if (-not (Test-Path -LiteralPath $NasRoot -PathType Container)) {
    throw "Cannot access TrueNAS SMB folder: $NasRoot"
}
Push-Location $PSScriptRoot
try {
    $argsForPython = @('scripts/sync.py', '--root', $NasRoot, '--categories', $Categories)
    if ($SkipImageDecode) { $argsForPython += '--skip-image-decode' }
    if ($SyncProject) { $argsForPython += '--sync-project' }
    & py -3 @argsForPython
    if ($LASTEXITCODE -ne 0) { throw "YOLO tracker failed with exit code $LASTEXITCODE" }
    Write-Host 'Finished. README.md updated, docs/data.json ready, .reports generated.'
} finally {
    Pop-Location
}
