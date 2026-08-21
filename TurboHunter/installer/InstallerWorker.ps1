# Turbo Hunter 0.4.1 - installer worker
$ErrorActionPreference = 'Stop'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$Here = Split-Path -Parent $MyInvocation.MyCommand.Path
$InternalDir = Split-Path -Parent $Here
$BaseDir = Split-Path -Parent $InternalDir
$RuntimeDir = Join-Path $InternalDir 'runtime'
$PackagesDir = Join-Path $RuntimeDir 'packages'
$PrivatePythonDir = Join-Path $RuntimeDir 'python'
$PythonPathFile = Join-Path $RuntimeDir 'python_path.txt'
$InstallLog = Join-Path $RuntimeDir 'instalacao.log'
$StatusFile = Join-Path $RuntimeDir 'install_status.json'
$InstallOk = Join-Path $RuntimeDir 'install_ok.txt'
$StartTemplate = Join-Path $Here 'INICIAR_TEMPLATE.vbs'
$StartLauncher = Join-Path $BaseDir 'INICIAR TURBO HUNTER.vbs'
$InstallLauncher = Join-Path $BaseDir 'INSTALAR TURBO HUNTER.vbs'
$PythonInstaller = Join-Path $RuntimeDir 'python-3.14.7-amd64.exe'
$PythonUrl = 'https://www.python.org/ftp/python/3.14.7/python-3.14.7-amd64.exe'
$PythonSha256 = '9d9eb2709ef81bf5cd30db3c2096bdbc4ea10087c22e62f27d356b36f6ae9649'
$FridaVersion = '17.17.0'

$cultureName = [Globalization.CultureInfo]::CurrentUICulture.Name
$IsPt = $cultureName.ToLowerInvariant().StartsWith('pt')
function T([string]$Pt, [string]$En) { if ($IsPt) { return $Pt } return $En }

New-Item -ItemType Directory -Force -Path $RuntimeDir | Out-Null

function Write-Log([string]$Text) {
    $stamp = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
    Add-Content -LiteralPath $InstallLog -Value "[$stamp] $Text" -Encoding UTF8
}

function Set-Status([string]$State, [string]$Title, [string]$Detail, [int]$Step = 0) {
    $obj = [ordered]@{ state=$State; title=$Title; detail=$Detail; step=$Step; time=(Get-Date).ToString('o') }
    $json = $obj | ConvertTo-Json -Compress
    [System.IO.File]::WriteAllText($StatusFile, $json, [System.Text.Encoding]::Unicode)
    Write-Log "$Title - $Detail"
}

function Test-PythonExe([string]$Exe) {
    if ([string]::IsNullOrWhiteSpace($Exe) -or -not (Test-Path -LiteralPath $Exe)) { return $false }
    try {
        & $Exe -c "import sys, tkinter; raise SystemExit(0 if sys.version_info >= (3, 9) else 2)" *> $null
        return ($LASTEXITCODE -eq 0)
    } catch { return $false }
}

function Find-Python {
    if (Test-Path -LiteralPath $PythonPathFile) {
        $saved = (Get-Content -LiteralPath $PythonPathFile -Raw -ErrorAction SilentlyContinue).Trim()
        if (Test-PythonExe $saved) { return $saved }
    }

    $pm = Get-Command pymanager.exe -ErrorAction SilentlyContinue
    if ($pm) {
        try {
            $p = (& $pm.Source list --one 3 --format=exe 2>$null | Select-Object -First 1)
            if ($p) { $p = $p.ToString().Trim(' ', '"') }
            if (Test-PythonExe $p) { return $p }
        } catch {}
    }

    $py = Get-Command py.exe -ErrorAction SilentlyContinue
    if ($py) {
        try {
            $p = (& $py.Source -3 -c "import sys, tkinter; print(sys.executable)" 2>$null | Select-Object -Last 1)
            if ($p) { $p = $p.ToString().Trim() }
            if (Test-PythonExe $p) { return $p }
        } catch {}
    }

    $pythonCmd = Get-Command python.exe -ErrorAction SilentlyContinue
    if ($pythonCmd) {
        try {
            $p = (& $pythonCmd.Source -c "import sys, tkinter; print(sys.executable)" 2>$null | Select-Object -Last 1)
            if ($p) { $p = $p.ToString().Trim() }
            if (Test-PythonExe $p) { return $p }
        } catch {}
    }

    $roots = @(
        (Join-Path $env:LOCALAPPDATA 'Python'),
        (Join-Path $env:LOCALAPPDATA 'Programs\Python')
    )
    foreach ($root in $roots) {
        if (-not (Test-Path -LiteralPath $root)) { continue }
        $dirs = Get-ChildItem -LiteralPath $root -Directory -ErrorAction SilentlyContinue | Sort-Object Name -Descending
        foreach ($dir in $dirs) {
            $candidate = Join-Path $dir.FullName 'python.exe'
            if (Test-PythonExe $candidate) { return $candidate }
        }
    }

    $private = Join-Path $PrivatePythonDir 'python.exe'
    if (Test-PythonExe $private) { return $private }
    return $null
}

function Ensure-Pip([string]$PythonExe) {
    & $PythonExe -m pip --version *>> $InstallLog
    if ($LASTEXITCODE -eq 0) { return }
    & $PythonExe -m ensurepip --upgrade *>> $InstallLog
    if ($LASTEXITCODE -ne 0) {
        throw (T 'O Python foi encontrado, mas o pip não pôde ser preparado.' 'Python was found, but pip could not be prepared.')
    }
}

try {
    if (Test-Path -LiteralPath $StatusFile) { Remove-Item -LiteralPath $StatusFile -Force -ErrorAction SilentlyContinue }
    Set-Status 'working' (T 'Etapa 1 de 3 - Python' 'Step 1 of 3 - Python') (T 'Procurando uma instalação compatível do Python 3...' 'Looking for a compatible Python 3 installation...') 1

    $PythonExe = Find-Python
    if (-not $PythonExe) {
        Set-Status 'working' (T 'Etapa 1 de 3 - Python' 'Step 1 of 3 - Python') (T 'Baixando Python 3.14.7 oficial. Aguarde...' 'Downloading official Python 3.14.7. Please wait...') 1
        if (Test-Path -LiteralPath $PythonInstaller) { Remove-Item -LiteralPath $PythonInstaller -Force -ErrorAction SilentlyContinue }
        $wc = New-Object System.Net.WebClient
        $wc.Headers.Add('User-Agent', 'TurboHunter/0.4.1')
        $wc.DownloadFile($PythonUrl, $PythonInstaller)
        $hash = (Get-FileHash -Algorithm SHA256 -LiteralPath $PythonInstaller).Hash.ToLowerInvariant()
        if ($hash -ne $PythonSha256) {
            throw (T 'A verificação de segurança do instalador do Python falhou.' 'The Python installer security verification failed.')
        }

        Set-Status 'working' (T 'Etapa 1 de 3 - Python' 'Step 1 of 3 - Python') (T 'Instalando uma cópia privada do Python para o Turbo Hunter...' 'Installing a private Python copy for Turbo Hunter...') 1
        if (Test-Path -LiteralPath $PrivatePythonDir) { Remove-Item -LiteralPath $PrivatePythonDir -Recurse -Force -ErrorAction SilentlyContinue }
        New-Item -ItemType Directory -Force -Path $PrivatePythonDir | Out-Null
        $arguments = '/quiet InstallAllUsers=0 TargetDir="' + $PrivatePythonDir + '" PrependPath=0 AppendPath=0 Include_launcher=0 Include_pip=1 Include_tcltk=1 Include_test=0 Include_doc=0 Shortcuts=0 AssociateFiles=0'
        $proc = Start-Process -FilePath $PythonInstaller -ArgumentList $arguments -PassThru -Wait -WindowStyle Hidden
        if ($proc.ExitCode -ne 0) {
            throw ((T 'A instalação do Python terminou com código ' 'Python installation ended with code ') + $proc.ExitCode + '.')
        }
        $PythonExe = Join-Path $PrivatePythonDir 'python.exe'
        if (-not (Test-PythonExe $PythonExe)) {
            throw (T 'O Python foi instalado, mas não passou na verificação final.' 'Python was installed but failed the final verification.')
        }
    }

    Set-Status 'working' (T 'Etapa 2 de 3 - Componentes' 'Step 2 of 3 - Components') (T 'Python pronto. Preparando o Frida...' 'Python is ready. Preparing Frida...') 2
    [System.IO.File]::WriteAllText($PythonPathFile, $PythonExe, [System.Text.Encoding]::Unicode)
    Ensure-Pip $PythonExe

    if (Test-Path -LiteralPath $PackagesDir) { Remove-Item -LiteralPath $PackagesDir -Recurse -Force -ErrorAction SilentlyContinue }
    New-Item -ItemType Directory -Force -Path $PackagesDir | Out-Null
    Set-Status 'working' (T 'Etapa 2 de 3 - Componentes' 'Step 2 of 3 - Components') ((T 'Baixando e instalando Frida ' 'Downloading and installing Frida ') + $FridaVersion + '...') 2
    & $PythonExe -m pip install --disable-pip-version-check --no-input --upgrade --target $PackagesDir "frida==$FridaVersion" *>> $InstallLog
    if ($LASTEXITCODE -ne 0) {
        throw (T 'Não foi possível baixar ou instalar o Frida. Verifique a internet ou o bloqueio da rede.' 'Frida could not be downloaded or installed. Check your internet connection or network restrictions.')
    }

    $oldPythonPath = $env:PYTHONPATH
    $env:PYTHONPATH = $PackagesDir
    & $PythonExe -c "import frida, tkinter; print(frida.__version__)" *>> $InstallLog
    $verifyExit = $LASTEXITCODE
    $env:PYTHONPATH = $oldPythonPath
    if ($verifyExit -ne 0) {
        throw (T 'O Frida foi baixado, mas a verificação final falhou.' 'Frida was downloaded, but the final verification failed.')
    }

    Set-Status 'working' (T 'Etapa 3 de 3 - Finalizando' 'Step 3 of 3 - Finishing') (T 'Criando o iniciador do Turbo Hunter...' 'Creating the Turbo Hunter launcher...') 3
    Copy-Item -LiteralPath $StartTemplate -Destination $StartLauncher -Force
    [System.IO.File]::WriteAllText($InstallOk, "Turbo Hunter 0.4.1`r`n", [System.Text.Encoding]::Unicode)
    Remove-Item -LiteralPath $PythonInstaller -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $InstallLauncher -Force -ErrorAction SilentlyContinue

    Set-Status 'done' (T 'Instalação concluída' 'Installation complete') (T 'Tudo pronto. Clique em ABRIR TURBO HUNTER.' 'Everything is ready. Click OPEN TURBO HUNTER.') 3
} catch {
    Write-Log ('ERROR/ERRO: ' + $_.Exception.ToString())
    Set-Status 'error' (T 'Não foi possível concluir' 'Could not complete installation') $_.Exception.Message 0
    exit 1
}
