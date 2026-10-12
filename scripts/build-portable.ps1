param(
    [string]$Python = 'python',
    [string]$Version = '2.0.2'
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$bits = & $Python -c "import struct; print(struct.calcsize('P') * 8)"
if ($LASTEXITCODE -ne 0 -or $bits.Trim() -ne '32') { throw 'Для сборки требуется CPython 3.12 x86 (32-bit).' }
$pyver = & $Python -c "import sys; print('.'.join(map(str, sys.version_info[:2])))"
if ($pyver.Trim() -ne '3.12') { throw 'Для сборки требуется CPython 3.12 x86.' }

& $Python -m pip install --disable-pip-version-check --require-hashes -r (Join-Path $root 'packaging\requirements-build.lock')
if ($LASTEXITCODE -ne 0) { throw 'Не удалось проверить build dependencies.' }

$build = Join-Path $root 'build\pyinstaller'
$dist = Join-Path $root 'dist'
$releaseName = "DPS-XML-Recovery-$Version-win-x86"
$release = Join-Path $dist $releaseName
if (Test-Path $build) { Remove-Item -LiteralPath $build -Recurse -Force }
if (Test-Path $dist) { Remove-Item -LiteralPath $dist -Recurse -Force }
New-Item -ItemType Directory -Path $build,$dist | Out-Null

Push-Location $root
try {
    & $Python -m PyInstaller --clean --noconfirm --distpath $dist --workpath $build 'packaging\dps_xml_recovery.spec'
    if ($LASTEXITCODE -ne 0) { throw 'PyInstaller завершился с ошибкой.' }
} finally { Pop-Location }

$raw = Join-Path $dist 'DPSXmlRecovery'
if (-not (Test-Path $raw)) { throw 'Каталог onedir не создан.' }
Move-Item -LiteralPath $raw -Destination $release
Copy-Item (Join-Path $root 'README-PORTABLE.md') (Join-Path $release 'README.txt')
Copy-Item (Join-Path $root 'README.md') $release
Copy-Item (Join-Path $root 'README.ru.md') $release
Copy-Item (Join-Path $root 'CHANGELOG.md') $release
Copy-Item (Join-Path $root 'THIRD-PARTY-NOTICES.txt') $release
Copy-Item (Join-Path $root 'LICENSE') $release
Copy-Item (Join-Path $root 'packaging\sbom.json') $release
New-Item -ItemType Directory -Path (Join-Path $release 'portable-data') | Out-Null
Copy-Item (Join-Path $root 'packaging\portable-data-README.txt') (Join-Path $release 'portable-data\README.txt')

$pythonRoot = (& $Python -c "import sys; print(sys.base_prefix)").Trim()
$sitePackages = (& $Python -c "import sysconfig; print(sysconfig.get_paths()['purelib'])").Trim()
$licenses = Join-Path $release 'licenses'
New-Item -ItemType Directory -Path $licenses | Out-Null
Copy-Item (Join-Path $pythonRoot 'LICENSE.txt') (Join-Path $licenses 'CPython-LICENSE.txt')
Copy-Item (Join-Path $release '_internal\_tk_data\license.terms') (Join-Path $licenses 'TclTk-LICENSE.txt')
Copy-Item (Join-Path $sitePackages 'pyinstaller-6.22.3.dist-info\licenses\COPYING.txt') (Join-Path $licenses 'PyInstaller-LICENSE.txt')
Copy-Item (Join-Path $sitePackages 'pyinstaller_hooks_contrib-2026.8.dist-info\licenses\LICENSE') (Join-Path $licenses 'pyinstaller-hooks-contrib-LICENSE.txt')
Copy-Item (Join-Path $sitePackages 'certifi-2026.7.22.dist-info\licenses\LICENSE') (Join-Path $licenses 'certifi-LICENSE.txt')
Copy-Item (Join-Path $sitePackages 'altgraph-0.17.5.dist-info\LICENSE') (Join-Path $licenses 'altgraph-LICENSE.txt')
Copy-Item (Join-Path $sitePackages 'packaging-26.3.dist-info\licenses\LICENSE') (Join-Path $licenses 'packaging-LICENSE.txt')
Copy-Item (Join-Path $sitePackages 'pefile-2024.8.26.dist-info\LICENSE') (Join-Path $licenses 'pefile-LICENSE.txt')
Copy-Item (Join-Path $sitePackages 'pywin32_ctypes-0.2.3.dist-info\LICENSE.txt') (Join-Path $licenses 'pywin32-ctypes-LICENSE.txt')
Copy-Item (Join-Path $sitePackages 'setuptools-84.0.0.dist-info\licenses\LICENSE') (Join-Path $licenses 'setuptools-LICENSE.txt')

$zip = Join-Path $dist "$releaseName.zip"
Compress-Archive -LiteralPath $release -DestinationPath $zip -CompressionLevel Optimal
$env:PYTHONPATH = $root
& $Python -m dps_gui.package_policy --root $release --archive $zip --version $Version --report (Join-Path $dist 'package-verification.json')
if ($LASTEXITCODE -ne 0) { throw 'Проверка состава release не пройдена.' }
$hash = (Get-FileHash -Algorithm SHA256 -LiteralPath $zip).Hash.ToLowerInvariant()
Set-Content -LiteralPath (Join-Path $dist 'SHA256SUMS.txt') -Encoding ascii -Value "$hash  $releaseName.zip"
Write-Host "Release: $release"
Write-Host "ZIP: $zip"
Write-Host "SHA-256: $hash"
