$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$env:PYINSTALLER_CONFIG_DIR = Join-Path $PSScriptRoot '.pyinstaller-cache'
$projectRoot = [IO.Path]::GetFullPath($PSScriptRoot).TrimEnd('\') + '\'
# PyInstaller cleans/replaces these directories. Check their resolved boundaries first.
foreach ($relative in @('.pyinstaller-cache', 'build', 'dist')) {
    $target = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot $relative))
    if (-not $target.StartsWith($projectRoot, [StringComparison]::OrdinalIgnoreCase)) {
        throw 'Build output must remain inside this project.'
    }
}
$previousPath = $env:PATH
try {
    # Do not bundle an unrelated ICU/Qt DLL found in another application's PATH.
    $systemDirectory = [Environment]::GetFolderPath('System')
    $windowsDirectory = Split-Path -Parent $systemDirectory
    $env:PATH = (Join-Path $PSScriptRoot '.venv\Scripts'), $systemDirectory, $windowsDirectory -join ';'
    & '.\.venv\Scripts\python.exe' scripts/build.py
    if ($LASTEXITCODE -ne 0) { throw 'Build failed.' }
}
finally {
    $env:PATH = $previousPath
}
& '.\.venv\Scripts\python.exe' scripts/smoke_package.py
if ($LASTEXITCODE -ne 0) { throw 'Packaged application smoke test failed.' }
