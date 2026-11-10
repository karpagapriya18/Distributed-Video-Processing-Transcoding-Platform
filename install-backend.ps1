param([string]$ProjectPath = 'D:\video-platform')
$ErrorActionPreference = 'Stop'
$project = (Resolve-Path -LiteralPath $ProjectPath).Path
if (!(Test-Path (Join-Path $project 'compose.yaml'))) { throw 'Select the project folder containing compose.yaml.' }
$entries = Get-Content (Join-Path $PSScriptRoot 'manifest.json') -Raw | ConvertFrom-Json
$pending = @()
foreach ($entry in $entries) {
    $source = Join-Path (Join-Path $PSScriptRoot 'files') $entry.path
    if ((Get-FileHash -LiteralPath $source -Algorithm SHA256).Hash.ToLower() -ne $entry.after) { throw "Package file failed validation: $($entry.path)" }
    $target = Join-Path $project $entry.path
    if (Test-Path -LiteralPath $target) {
        $actual = (Get-FileHash -LiteralPath $target -Algorithm SHA256).Hash.ToLower()
        if ($actual -eq $entry.after) { continue }
        if (!$entry.before -or $actual -ne $entry.before) { throw "Local edits detected in $($entry.path). Nothing has been copied. Share this file for a safe merge." }
    } elseif ($entry.before) { throw "Expected project file missing: $($entry.path). Nothing has been copied." }
    $pending += $entry
}
if ($pending.Count -eq 0) { Write-Host 'This backend update is already installed.'; exit 0 }
$backup = Join-Path (Split-Path $project -Parent) ('video-platform-backend-backup-' + (Get-Date -Format 'yyyyMMdd-HHmmss-fff'))
New-Item -ItemType Directory -Path $backup | Out-Null
foreach ($entry in $pending) {
    $target = Join-Path $project $entry.path
    if (Test-Path -LiteralPath $target) {
        $saved = Join-Path $backup $entry.path
        New-Item -ItemType Directory -Force -Path (Split-Path $saved -Parent) | Out-Null
        Copy-Item -LiteralPath $target -Destination $saved
    }
}
foreach ($entry in $pending) {
    $target = Join-Path $project $entry.path
    New-Item -ItemType Directory -Force -Path (Split-Path $target -Parent) | Out-Null
    Copy-Item -LiteralPath (Join-Path (Join-Path $PSScriptRoot 'files') $entry.path) -Destination $target -Force
}
Write-Host "Backend source updated. Backup: $backup"
Write-Host 'Next: rebuild backend containers using READ-ME-FIRST.md.'
