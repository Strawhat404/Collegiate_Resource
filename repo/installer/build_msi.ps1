# Build script for the CRHGC Windows Installer.
#
# Prereqs (Windows host):
#   - Python 3.11+ with venv
#   - pip install -r requirements.txt pyinstaller
#   - WiX Toolset v3.11+ on PATH (candle.exe / light.exe)
#
# Run from the `repo/` directory:
#   powershell -ExecutionPolicy Bypass -File installer\build_msi.ps1

$ErrorActionPreference = "Stop"

Write-Host "==> Cleaning prior build outputs"
Remove-Item -Recurse -Force build, dist 2>$null

Write-Host "==> Running PyInstaller"
pyinstaller --noconfirm --windowed --name CRHGC `
            --add-data "frontend\style.qss;frontend" `
            --add-data "database\migrations;database\migrations" `
            --add-data "database\seed;database\seed" `
            main.py

Write-Host "==> Validating signed-update public key"
$pubKey  = "installer\update_pubkey.pem"
$example = "installer\update_pubkey.pem.example"
$candleArgs = @("-arch", "x64")
if (Test-Path $pubKey) {
    $bytes = [IO.File]::ReadAllBytes($pubKey)
    $text  = [Text.Encoding]::ASCII.GetString($bytes)
    if ($text -match "PLACEHOLDER" -or $text -match "EXAMPLE" `
            -or -not ($text -match "-----BEGIN PUBLIC KEY-----")) {
        throw ("$pubKey looks like a placeholder. Replace it with a real " +
               "RSA-3072 SPKI PEM (see $example) before producing a release " +
               "MSI; refusing to ship an installer with an unverifiable key.")
    }
    Write-Host "    Production public key found — bundling into MSI."
    $candleArgs += @("-dHasUpdatePubKey=1")
} else {
    Write-Warning ("No production update_pubkey.pem present. Building an " +
                   "MSI WITHOUT a bundled signing key — the deployed app " +
                   "will reject every update with SIGNATURE_REQUIRED until " +
                   "an operator drops in a real key at " +
                   "%LOCALAPPDATA%\CRHGC\update_pubkey.pem. See $example.")
}

Write-Host "==> Compiling WiX source"
candle @candleArgs installer\CRHGC.wxs -o installer\CRHGC.wixobj

Write-Host "==> Linking MSI"
light -ext WixUIExtension installer\CRHGC.wixobj -o installer\CRHGC.msi

Write-Host "==> Done. installer\CRHGC.msi"
