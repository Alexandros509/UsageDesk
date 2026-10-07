$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath (Split-Path -Parent $PSScriptRoot)
New-Item -ItemType Directory -Path '.test-data' -Force | Out-Null
# A fresh workspace-local directory avoids clearing any existing test evidence.
$testBase = Join-Path (Get-Location) ('.test-data\run-' + [guid]::NewGuid().ToString('N'))
& '.\.venv\Scripts\python.exe' -m pytest -q -p no:cacheprovider --basetemp $testBase
if ($LASTEXITCODE -ne 0) { throw 'Tests failed.' }
& '.\.venv\Scripts\python.exe' -m ruff check src tests scripts packaging
if ($LASTEXITCODE -ne 0) { throw 'Lint failed.' }
