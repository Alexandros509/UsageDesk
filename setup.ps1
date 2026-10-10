param([string]$Python = '')
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$env:UV_CACHE_DIR = Join-Path $PSScriptRoot '.uv-cache'
$env:UV_PYTHON_INSTALL_DIR = Join-Path $PSScriptRoot '.python'
if (-not $Python) {
    $officialPython = Join-Path $PSScriptRoot '.python\official-3.13.15\python.exe'
    if (Test-Path -LiteralPath $officialPython) { $Python = $officialPython }
}
if ($Python) {
    uv sync --locked --python $Python
} else {
    uv sync --locked
}
if ($LASTEXITCODE -ne 0) { throw 'Dependency setup failed.' }
