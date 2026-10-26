param([switch]$SettingsOnly)
$ErrorActionPreference = 'Stop'
$internal = Split-Path -Parent $PSScriptRoot
$root = Split-Path -Parent $internal
$pythonw = Join-Path $internal 'runtime\python-portable\pythonw.exe'
try {
    if (Test-Path -LiteralPath (Join-Path $root '.maintenance')) { throw 'Instalacao ou desinstalacao em andamento.' }
    if (-not (Test-Path -LiteralPath (Join-Path $internal 'runtime\install_ok.txt')) -or -not (Test-Path -LiteralPath $pythonw)) {
        throw 'Execute INSTALL TURBO HUNTER.cmd no ZIP extraido para instalar ou reparar.'
    }
    $env:PYTHONHOME = $null; $env:PYTHONPATH = $null; $env:PYTHONNOUSERSITE = '1'
    $gui = Join-Path $internal 'app\TurboHunter.pyw'
    $arguments = '-s "' + $gui + '"'
    if ($SettingsOnly) { $arguments += ' --settings' }
    Start-Process -FilePath $pythonw -ArgumentList $arguments -WorkingDirectory $root | Out-Null
} catch {
    Add-Type -AssemblyName System.Windows.Forms
    [Windows.Forms.MessageBox]::Show($_.Exception.Message, 'Turbo Hunter 0.6.0', 'OK', 'Error') | Out-Null
    exit 1
}
