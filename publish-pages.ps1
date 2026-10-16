<#
Run on a Windows computer which has read-only SMB access to TrueNAS.
Scan -> write privacy-filtered dashboard -> commit ONLY docs/data.json + README -> push.
GitHub-hosted Actions handles GitHub Pages publication after the push.
Use Task Scheduler with the same Windows account that can access the NAS and Git credentials.
#>
param(
    [Parameter(Mandatory = $true)][string]$NasRoot,
    [string]$Categories = 'parts,abnormal',
    [string]$SiteUrl = '',
    [switch]$SkipImageDecode,
    [switch]$ExposeNames,
    [switch]$NoPush
)
$ErrorActionPreference = 'Stop'
Push-Location $PSScriptRoot
try {
    if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
        throw 'Git for Windows must be installed and available on PATH.'
    }
    $branch = (& git branch --show-current).Trim()
    if ($LASTEXITCODE -ne 0 -or $branch -ne 'main') {
        throw "Check out the main branch before publishing (currently: $branch)."
    }
    # Avoid committing any unrelated staged changes accidentally.
    $alreadyStaged = @(& git diff --cached --name-only)
    if ($LASTEXITCODE -ne 0) { throw 'Cannot inspect the Git staging area.' }
    if ($alreadyStaged.Count -gt 0) {
        throw 'There are already staged changes. Commit or unstage them before publishing.'
    }
    $remote = (& git remote get-url origin).Trim()
    if ($LASTEXITCODE -ne 0) { throw 'Configure an origin remote pointing to your GitHub repository.' }
    if ([string]::IsNullOrWhiteSpace($SiteUrl)) {
        if ($remote -match 'github\.com[:/](?<owner>[^/]+)/(?<repo>[^/\s]+)$') {
            $owner = $Matches['owner']
            $repo = ($Matches['repo'] -replace '\.git$', '')
            if ($repo -ieq "$owner.github.io") {
                $SiteUrl = "https://$owner.github.io/"
            } else {
                $SiteUrl = "https://$owner.github.io/$repo/"
            }
        }
    }
    $env:DASHBOARD_URL = $SiteUrl
    # Privacy-safe DEFAULT: -ExposeNames is an explicit opt-in for public filenames/classes.
    if ($ExposeNames) { $env:DASHBOARD_EXPOSE_NAMES = 'true' }
    else { $env:DASHBOARD_EXPOSE_NAMES = 'false' }

    $params = @{ NasRoot = $NasRoot; Categories = $Categories }
    if ($SkipImageDecode) { $params.SkipImageDecode = $true }
    & "$PSScriptRoot/run-scan.ps1" @params
    if ($LASTEXITCODE -and $LASTEXITCODE -ne 0) { throw "Scanner failed: $LASTEXITCODE" }
    if (-not (Test-Path -LiteralPath 'docs/data.json' -PathType Leaf)) {
        throw 'Scanner did not generate docs/data.json.'
    }
    if ($NoPush) {
        Write-Host 'Local scan complete. No files pushed because -NoPush was provided.'
        return
    }
    & git add -- README.md docs/data.json
    if ($LASTEXITCODE -ne 0) { throw 'Failed to stage public dashboard files.' }
    & git diff --cached --quiet
    if ($LASTEXITCODE -eq 0) {
        Write-Host 'Dashboard is already current; nothing to push.'
        return
    }
    if ($LASTEXITCODE -ne 1) { throw 'Failed to inspect dashboard changes.' }
    & git -c 'user.name=TrueNAS Dashboard Bot' -c 'user.email=truenas-dashboard@users.noreply.github.com' commit -m 'data: refresh YOLO dashboard'
    if ($LASTEXITCODE -ne 0) { throw 'Failed to commit sanitized dashboard files.' }
    & git push origin main
    if ($LASTEXITCODE -ne 0) { throw 'Git push failed. Check remote branch, connectivity and Git authentication.' }
    Write-Host "Published sanitized statistics to GitHub. GitHub Actions will deploy Pages: $SiteUrl"
} finally {
    Pop-Location
}
