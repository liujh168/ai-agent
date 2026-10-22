# Build the course center and sync it to ai_agent/static/courses/
# (ASCII-only: Windows PowerShell 5.1 misreads UTF-8-no-BOM .ps1 files)
$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot

npm run build
if ($LASTEXITCODE -ne 0) { throw "npm run build failed" }

$target = Join-Path $PSScriptRoot '..\ai_agent\static\courses'
if (Test-Path $target) { Remove-Item -Recurse -Force $target }
Copy-Item -Recurse (Join-Path $PSScriptRoot 'dist') $target

Write-Host "[ok] synced to $target - restart the Web UI to take effect" -ForegroundColor Green
