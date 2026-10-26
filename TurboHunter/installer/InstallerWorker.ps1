param([string]$PayloadDir = '')
. (Join-Path $PSScriptRoot 'Common.ps1')
$Internal = Split-Path -Parent $PSScriptRoot
$Root = Split-Path -Parent $Internal
Assert-TurboRoot $Root
$Runtime = Join-Path $Internal 'runtime'
$PythonDir = Join-Path $Runtime 'python-portable'
$InstallLog = Join-Path $Root 'install_log.txt'
$Ok = Join-Path $Runtime 'install_ok.txt'
$stage = Join-Path $Runtime ('stage-' + [guid]::NewGuid().ToString('N'))
$oldEnvironment = @{}
foreach ($name in @('PYTHONHOME','PYTHONPATH','PYTHONNOUSERSITE','PIP_CONFIG_FILE','PYTHONDONTWRITEBYTECODE','PYTHONUNBUFFERED')) {
    $oldEnvironment[$name] = [Environment]::GetEnvironmentVariable($name, 'Process')
}
$env:PYTHONHOME = $null; $env:PYTHONPATH = $null
$env:PYTHONNOUSERSITE = '1'; $env:PIP_CONFIG_FILE = 'NUL'; $env:PYTHONDONTWRITEBYTECODE = '1'
$env:PYTHONUNBUFFERED = '1'
function Write-Log([string]$Text) {
    Write-InstallStatus $Root $Text
}
function Invoke-Python([string]$Exe, [string[]]$Arguments) {
    $saved = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        & $Exe @Arguments 2>&1 | ForEach-Object {
            $line = [string]$_; Add-Content -LiteralPath $InstallLog -Encoding UTF8 -Value $line
        }
        return $LASTEXITCODE
    } finally { $ErrorActionPreference = $saved }
}
function Test-Runtime([string]$Directory) {
    $exe = Join-Path $Directory 'python.exe'
    if (-not (Test-Path -LiteralPath $exe) -or -not (Test-Path -LiteralPath (Join-Path $Directory 'pythonw.exe'))) { return $false }
    $code = "import sys,tkinter,frida,importlib.metadata; assert sys.version_info[:3] == (3,13,16); assert sys.maxsize > 2**32; assert callable(frida.attach); assert importlib.metadata.version('frida') == '17.17.0'; t=tkinter.Tk(); t.withdraw(); t.update_idletasks(); t.destroy()"
    return (Invoke-Python $exe @('-I', '-c', $code)) -eq 0
}
try {
    New-Item -ItemType Directory -Force -Path $Runtime | Out-Null
    Remove-Item -LiteralPath $Ok -Force -ErrorAction SilentlyContinue
    Write-Log (T 'Preparando Python e bibliotecas...' 'Preparing Python and libraries...')
    if (-not [Environment]::Is64BitOperatingSystem) { throw 'Turbo Hunter requer Windows 64 bits.' }
    if (-not (Test-Runtime $PythonDir)) {
        New-Item -ItemType Directory -Path $stage | Out-Null
        $manifest = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'payload_manifest.json') -Raw | ConvertFrom-Json
        foreach ($item in @($manifest.python, $manifest.frida)) {
            $label = if ($item.filename -eq $manifest.python.filename) { 'Python 3.13.16' } else { 'Frida 17.17.0' }
            $destination = Join-Path $stage $item.filename
            $bundled = if ($PayloadDir) { Join-Path $PayloadDir $item.filename } else { '' }
            if ($bundled -and (Test-Path -LiteralPath $bundled)) {
                Write-Log ((T 'Preparando ' 'Preparing ') + $label + '...')
                Copy-Item -LiteralPath $bundled -Destination $destination
                if ((Get-FileHash -LiteralPath $destination -Algorithm SHA256).Hash -ine $item.sha256) { throw 'Arquivo do pacote danificado (SHA256).' }
            } else {
                Write-Log ((T 'Baixando ' 'Downloading ') + $label + '...')
                Download-Verified -Url $item.url -Destination $destination -Hash $item.sha256 -SizeBytes $item.size_bytes -Label $label
            }
        }
        Write-Log (T 'Preparando Python...' 'Setting up Python...')
        $tar = Join-Path $env:SystemRoot 'System32\tar.exe'
        if (-not (Test-Path -LiteralPath $tar)) { throw 'Esta instalacao requer Windows 10/11 com tar.exe.' }
        & $tar -xf (Join-Path $stage $manifest.python.filename) -C $stage
        if ($LASTEXITCODE -ne 0) { throw 'Nao foi possivel extrair o Python.' }
        $stagedPython = Join-Path $stage 'python'
        $exe = Join-Path $stagedPython 'python.exe'
        if (-not (Test-Path -LiteralPath $exe)) { throw 'Python nao encontrado no pacote.' }
        Write-Log (T 'Python pronto.' 'Python is ready.')
        Write-Log (T 'Instalando a biblioteca Frida...' 'Installing the Frida library...')
        $wheel = Join-Path $stage $manifest.frida.filename
        if ((Invoke-Python $exe @('-I','-m','pip','--isolated','install','--no-index','--no-deps','--no-cache-dir','--disable-pip-version-check',$wheel)) -ne 0) { throw 'Nao foi possivel instalar Frida.' }
        Write-Log (T 'Conferindo os componentes...' 'Checking the components...')
        if ((Invoke-Python $exe @('-I','-m','pip','--isolated','check')) -ne 0) { throw 'Os componentes possuem dependencias incompletas.' }
        if (-not (Test-Runtime $stagedPython)) { throw 'Python, Tcl/Tk ou Frida nao passaram na verificacao final.' }
        if (Test-Path -LiteralPath $PythonDir) { Remove-Item -LiteralPath $PythonDir -Recurse -Force }
        Move-Item -LiteralPath $stagedPython -Destination $PythonDir
    }
    Write-Log (T 'Finalizando a instalacao...' 'Finishing installation...')
    [IO.File]::WriteAllText((Join-Path $Runtime 'python_path.txt'), (Join-Path $PythonDir 'python.exe'), [Text.Encoding]::Unicode)
    $launcher = Join-Path $Root 'INICIAR TURBO HUNTER.vbs'
    Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'INICIAR_TEMPLATE.vbs') -Destination $launcher -Force
    $cmdLauncher = @'
@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "%~dp0TurboHunter\installer\LaunchTogether.ps1"
exit /b %ERRORLEVEL%
'@
    [IO.File]::WriteAllText((Join-Path $Root 'START TURBO HUNTER.cmd'), $cmdLauncher, [Text.Encoding]::ASCII)
    Remove-TurboShortcuts $Root
    $shortcuts = @()
    $shell = New-Object -ComObject WScript.Shell
    foreach ($folder in @([Environment]::GetFolderPath('DesktopDirectory'), [Environment]::GetFolderPath('Programs'))) {
        $shortcutPath = Join-Path $folder 'Turbo Hunter.lnk'
        $shortcut = $shell.CreateShortcut($shortcutPath)
        $shortcut.TargetPath = (Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe')
        $shortcut.Arguments = '-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "' + (Join-Path $PSScriptRoot 'LaunchTogether.ps1') + '"'
        $shortcut.WorkingDirectory = $Root
        $shortcut.IconLocation = Join-Path $Internal 'assets\turbo_hunter.ico'
        $shortcut.Description = 'Turbo Hunter 0.6.0'; $shortcut.WindowStyle = 7; $shortcut.Save()
        $shortcuts += $shortcutPath
    }
    $record = @{version='0.6.0'; runtime='portable'; python='3.13.16'; frida='17.17.0'; shortcuts=$shortcuts}
    $record | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $Runtime 'ownership.json') -Encoding UTF8
    $key = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\TurboHunter'
    New-Item -Path $key -Force | Out-Null
    New-ItemProperty -Path $key -Name DisplayName -Value 'Turbo Hunter' -PropertyType String -Force | Out-Null
    New-ItemProperty -Path $key -Name DisplayVersion -Value '0.6.0' -PropertyType String -Force | Out-Null
    New-ItemProperty -Path $key -Name Publisher -Value 'DavidCPU' -PropertyType String -Force | Out-Null
    New-ItemProperty -Path $key -Name InstallLocation -Value $Root -PropertyType String -Force | Out-Null
    $uninstall = '"' + (Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe') + '" -NoProfile -ExecutionPolicy Bypass -File "' + (Join-Path $PSScriptRoot 'Uninstall.ps1') + '"'
    New-ItemProperty -Path $key -Name UninstallString -Value $uninstall -PropertyType String -Force | Out-Null
    New-ItemProperty -Path $key -Name DisplayIcon -Value (Join-Path $Internal 'assets\turbo_hunter.ico') -PropertyType String -Force | Out-Null
    [IO.File]::WriteAllText($Ok, 'Turbo Hunter 0.6.0')
    Write-Log (T 'Instalacao concluida: 100%. Abrindo as opcoes.' 'Installation complete: 100%. Opening settings.')
    Remove-Item -LiteralPath (Join-Path $Root '.maintenance') -Force -ErrorAction SilentlyContinue
    & (Join-Path $PSScriptRoot 'LaunchTogether.ps1') -SettingsOnly
    exit 0
} catch {
    Add-Content -LiteralPath $InstallLog -Encoding UTF8 -Value $_.Exception.ToString()
    Write-Log (T 'Nao foi possivel concluir a instalacao. Verifique a internet e tente novamente.' 'Could not complete installation. Check your connection and try again.')
    exit 1
} finally {
    if (Test-Path -LiteralPath $stage) { Remove-Item -LiteralPath $stage -Recurse -Force -ErrorAction SilentlyContinue }
    foreach ($name in $oldEnvironment.Keys) { [Environment]::SetEnvironmentVariable($name, $oldEnvironment[$name], 'Process') }
}
