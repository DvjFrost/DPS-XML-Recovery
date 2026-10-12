param([string]$Python = 'python')

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$bits = & $Python -c "import struct; print(struct.calcsize('P') * 8)"
if ($LASTEXITCODE -ne 0 -or $bits.Trim() -ne '32') { throw 'Для acceptance-сборки требуется CPython 3.12 x86.' }
$pyver = & $Python -c "import sys; print('.'.join(map(str, sys.version_info[:2])))"
if ($pyver.Trim() -ne '3.12') { throw 'Для acceptance-сборки требуется CPython 3.12 x86.' }

$build = Join-Path $root 'build\acceptance-pyinstaller'
$dist = Join-Path $root 'build\acceptance-dist'
if (Test-Path $build) { Remove-Item -LiteralPath $build -Recurse -Force }
if (Test-Path $dist) { Remove-Item -LiteralPath $dist -Recurse -Force }
New-Item -ItemType Directory -Path $build,$dist | Out-Null
Push-Location $root
try {
    & $Python -m PyInstaller --clean --noconfirm --distpath $dist --workpath $build 'packaging\dps_xml_recovery_acceptance.spec'
    if ($LASTEXITCODE -ne 0) { throw 'Acceptance PyInstaller завершился с ошибкой.' }
} finally { Pop-Location }

$exe = Join-Path $dist 'DPSXmlRecoveryAcceptance\DPSXmlRecoveryAcceptance.exe'
if (-not (Test-Path $exe)) { throw 'Acceptance EXE не создан.' }
Write-Host "Acceptance EXE: $exe"
