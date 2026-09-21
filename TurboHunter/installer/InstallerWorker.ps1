# Turbo Hunter 0.5.4 - installer worker
$ErrorActionPreference = 'Stop'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$Here = Split-Path -Parent $MyInvocation.MyCommand.Path
$InternalDir = Split-Path -Parent $Here
$BaseDir = Split-Path -Parent $InternalDir
$RuntimeDir = Join-Path $InternalDir 'runtime'
$PackagesDir = Join-Path $RuntimeDir 'packages'
$PackagesStagingDir = Join-Path $RuntimeDir 'packages.new'
$PackagesBackupDir = Join-Path $RuntimeDir 'packages.previous'
$PrivatePythonDir = Join-Path $RuntimeDir 'python'
$PythonPathFile = Join-Path $RuntimeDir 'python_path.txt'
$PythonPathTemp = Join-Path $RuntimeDir 'python_path.new.txt'
$InstallLog = Join-Path $RuntimeDir 'instalacao.log'
$PythonInstallLog = Join-Path $RuntimeDir 'python_installer.log'
$StatusFile = Join-Path $RuntimeDir 'install_status.json'
$InstallOk = Join-Path $RuntimeDir 'install_ok.txt'
$StartTemplate = Join-Path $Here 'INICIAR_TEMPLATE.vbs'
$StartLauncher = Join-Path $BaseDir 'INICIAR TURBO HUNTER.vbs'
$PythonInstaller = Join-Path $RuntimeDir 'python-3.14.7-amd64.exe'
$PythonUrl = 'https://www.python.org/ftp/python/3.14.7/python-3.14.7-amd64.exe'
$PythonSha256 = '9d9eb2709ef81bf5cd30db3c2096bdbc4ea10087c22e62f27d356b36f6ae9649'
$FridaVersion = '17.17.0'

$cultureName = [Globalization.CultureInfo]::CurrentUICulture.Name
$IsPt = $cultureName.ToLowerInvariant().StartsWith('pt')
function T([string]$Pt, [string]$En) { if ($IsPt) { return $Pt } return $En }

New-Item -ItemType Directory -Force -Path $RuntimeDir | Out-Null
$HadWorkingInstall = (
    (Test-Path -LiteralPath $InstallOk) -and
    (Test-Path -LiteralPath $PythonPathFile) -and
    (Test-Path -LiteralPath $PackagesDir)
)

function Write-Log([string]$Text) {
    $stamp = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
    Add-Content -LiteralPath $InstallLog -Value "[$stamp] $Text" -Encoding UTF8
    Write-Host "[$stamp] $Text"
}

function Invoke-Logged([string]$Exe, [string[]]$Arguments) {
    $savedPreference = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        # O pip tambem escreve avisos em stderr. Registrar cada linha como texto
        # evita que o formatador do PowerShell altere o traceback ou a codificacao.
        # Acompanhar a saída durante o download/instalação, sem ocultar os
        # passos até que o processo termine. O mesmo texto fica no arquivo.
        & $Exe @Arguments 2>&1 | ForEach-Object {
            $entry = $_
            $line = if ($entry -is [System.Management.Automation.ErrorRecord]) {
                $entry.Exception.Message
            } else {
                [string]$entry
            }
            Add-Content -LiteralPath $InstallLog -Value $line -Encoding UTF8
            Write-Host $line
        }
        return $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $savedPreference
    }
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

function Get-RegisteredPythonCandidates {
    $roots = @(
        'HKCU:\Software\Python\PythonCore',
        'HKLM:\Software\Python\PythonCore',
        'HKLM:\Software\WOW6432Node\Python\PythonCore'
    )
    $seen = @{}
    foreach ($root in $roots) {
        if (-not (Test-Path -LiteralPath $root)) { continue }
        foreach ($versionKey in Get-ChildItem -LiteralPath $root -ErrorAction SilentlyContinue) {
            $installKeyPath = Join-Path $versionKey.PSPath 'InstallPath'
            if (-not (Test-Path -LiteralPath $installKeyPath)) { continue }
            try {
                $installKey = Get-Item -LiteralPath $installKeyPath -ErrorAction Stop
                $candidate = [string]$installKey.GetValue('ExecutablePath', '')
                if ([string]::IsNullOrWhiteSpace($candidate)) {
                    $installDir = [string]$installKey.GetValue('', '')
                    if (-not [string]::IsNullOrWhiteSpace($installDir)) {
                        $candidate = Join-Path $installDir 'python.exe'
                    }
                }
                if (-not [string]::IsNullOrWhiteSpace($candidate)) {
                    $normalized = $candidate.Trim(' ', '"')
                    if (-not $seen.ContainsKey($normalized.ToLowerInvariant())) {
                        $seen[$normalized.ToLowerInvariant()] = $true
                        $normalized
                    }
                }
            } catch {}
        }
    }
}

function Write-PythonVerificationDetails([string]$Exe) {
    if ([string]::IsNullOrWhiteSpace($Exe) -or -not (Test-Path -LiteralPath $Exe)) {
        Write-Log (T 'Diagnóstico: python.exe não foi criado no destino privado.' 'Diagnostic: python.exe was not created in the private destination.')
        return
    }
    Write-Log (T 'Diagnóstico do núcleo do Python:' 'Python core diagnostic:')
    $coreExit = Invoke-Logged $Exe @('-c', 'import sys; print(sys.executable); print(sys.version)')
    Write-Log ((T 'Resultado do núcleo do Python: ' 'Python core result: ') + $coreExit)
    Write-Log (T 'Diagnóstico da interface Tkinter:' 'Tkinter diagnostic:')
    $tkExit = Invoke-Logged $Exe @('-c', 'import tkinter; print(tkinter.TkVersion)')
    Write-Log ((T 'Resultado do Tkinter: ' 'Tkinter result: ') + $tkExit)
}

function Install-PrivatePython {
    if (Test-Path -LiteralPath $PrivatePythonDir) {
        Remove-Item -LiteralPath $PrivatePythonDir -Recurse -Force -ErrorAction SilentlyContinue
    }
    New-Item -ItemType Directory -Force -Path $PrivatePythonDir | Out-Null
    if (Test-Path -LiteralPath $PythonInstallLog) {
        Remove-Item -LiteralPath $PythonInstallLog -Force -ErrorAction SilentlyContinue
    }
    $arguments = '/quiet /log "' + $PythonInstallLog + '" InstallAllUsers=0 TargetDir="' + $PrivatePythonDir + '" PrependPath=0 AppendPath=0 Include_launcher=0 Include_pip=1 Include_tcltk=1 Include_test=0 Include_doc=0 Shortcuts=0 AssociateFiles=0'
    $proc = Start-Process -FilePath $PythonInstaller -ArgumentList $arguments -PassThru -Wait -WindowStyle Hidden
    return [int]$proc.ExitCode
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

    foreach ($candidate in Get-RegisteredPythonCandidates) {
        if (Test-PythonExe $candidate) { return $candidate }
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
    $pipExit = Invoke-Logged $PythonExe @('-m', 'pip', '--version')
    if ($pipExit -eq 0) { return }
    $ensureExit = Invoke-Logged $PythonExe @('-m', 'ensurepip', '--upgrade')
    if ($ensureExit -ne 0) {
        throw (T 'O Python foi encontrado, mas o pip não pôde ser preparado.' 'Python was found, but pip could not be prepared.')
    }
}

try {
    if (Test-Path -LiteralPath $StatusFile) { Remove-Item -LiteralPath $StatusFile -Force -ErrorAction SilentlyContinue }
    # Recupera uma troca de componentes interrompida e preserva a versão que
    # já funcionava até os novos arquivos passarem em todos os testes.
    if (-not (Test-Path -LiteralPath $PackagesDir) -and (Test-Path -LiteralPath $PackagesBackupDir)) {
        Move-Item -LiteralPath $PackagesBackupDir -Destination $PackagesDir -Force
    } elseif ((Test-Path -LiteralPath $PackagesDir) -and (Test-Path -LiteralPath $PackagesBackupDir)) {
        Remove-Item -LiteralPath $PackagesBackupDir -Recurse -Force -ErrorAction SilentlyContinue
    }
    Remove-Item -LiteralPath $PackagesStagingDir -Recurse -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $PythonPathTemp -Force -ErrorAction SilentlyContinue
    Set-Status 'working' (T 'Etapa 1 de 3 - Python' 'Step 1 of 3 - Python') (T 'Procurando uma instalação compatível do Python 3...' 'Looking for a compatible Python 3 installation...') 1

    $PythonExe = Find-Python
    if (-not $PythonExe) {
        Set-Status 'working' (T 'Etapa 1 de 3 - Python' 'Step 1 of 3 - Python') (T 'Baixando Python 3.14.7 oficial. Aguarde...' 'Downloading official Python 3.14.7. Please wait...') 1
        if (Test-Path -LiteralPath $PythonInstaller) { Remove-Item -LiteralPath $PythonInstaller -Force -ErrorAction SilentlyContinue }
        $wc = New-Object System.Net.WebClient
        $wc.Headers.Add('User-Agent', 'TurboHunter/0.5.4')
        $wc.DownloadFile($PythonUrl, $PythonInstaller)
        $hash = (Get-FileHash -Algorithm SHA256 -LiteralPath $PythonInstaller).Hash.ToLowerInvariant()
        if ($hash -ne $PythonSha256) {
            throw (T 'A verificação de segurança do instalador do Python falhou.' 'The Python installer security verification failed.')
        }

        Set-Status 'working' (T 'Etapa 1 de 3 - Python' 'Step 1 of 3 - Python') (T 'Instalando uma cópia privada do Python para o Turbo Hunter...' 'Installing a private Python copy for Turbo Hunter...') 1
        $pythonInstallExit = Install-PrivatePython
        if ($pythonInstallExit -ne 0) {
            throw ((T 'A instalação do Python terminou com código ' 'Python installation ended with code ') + $pythonInstallExit + '.')
        }
        $PythonExe = Join-Path $PrivatePythonDir 'python.exe'
        if (-not (Test-PythonExe $PythonExe)) {
            Write-Log (T 'A cópia privada não passou no primeiro teste. Procurando uma instalação registrada pelo Windows...' 'The private copy failed its first test. Looking for a Python installation registered by Windows...')
            $registeredPython = Find-Python
            if ($registeredPython) {
                $PythonExe = $registeredPython
                Write-Log ((T 'Python compatível encontrado e confirmado em: ' 'Compatible Python found and verified at: ') + $PythonExe)
            } else {
                Write-PythonVerificationDetails $PythonExe
                Write-Log (T 'Removendo somente a cópia privada incompleta e tentando instalar mais uma vez...' 'Removing only the incomplete private copy and trying the installation once more...')
                $pythonInstallExit = Install-PrivatePython
                $PythonExe = Join-Path $PrivatePythonDir 'python.exe'
                if ($pythonInstallExit -ne 0 -or -not (Test-PythonExe $PythonExe)) {
                    Write-PythonVerificationDetails $PythonExe
                    throw (T 'O Python privado continuou incompleto após a segunda tentativa. Nenhum Python pessoal foi removido. Consulte python_installer.log.' 'The private Python remained incomplete after the second attempt. No personal Python installation was removed. Check python_installer.log.')
                }
            }
        }
    }

    Set-Status 'working' (T 'Etapa 2 de 3 - Componentes' 'Step 2 of 3 - Components') (T 'Python pronto. Preparando o Frida...' 'Python is ready. Preparing Frida...') 2
    Ensure-Pip $PythonExe

    New-Item -ItemType Directory -Force -Path $PackagesStagingDir | Out-Null
    Set-Status 'working' (T 'Etapa 2 de 3 - Componentes' 'Step 2 of 3 - Components') ((T 'Baixando e instalando Frida ' 'Downloading and installing Frida ') + $FridaVersion + '...') 2
    $installExit = Invoke-Logged $PythonExe @('-m', 'pip', 'install', '--disable-pip-version-check', '--no-input', '--upgrade', '--target', $PackagesStagingDir, "frida==$FridaVersion")
    if ($installExit -ne 0) {
        throw (T 'Não foi possível baixar o Frida. Verifique a internet e tente executar o instalador novamente.' 'Frida could not be downloaded. Check your internet connection and run the installer again.')
    }

    $oldPythonPath = $env:PYTHONPATH
    try {
        $env:PYTHONPATH = $PackagesStagingDir
        # Verificar a funcao realmente usada pelo mod, sem depender de
        # frida.__version__, que nao e necessaria para executar o programa.
        $verifyExit = Invoke-Logged $PythonExe @('-c', 'import frida, tkinter; assert callable(frida.attach); print(frida.__file__)')
    } finally {
        $env:PYTHONPATH = $oldPythonPath
    }
    if ($verifyExit -ne 0) {
        throw (T 'O Frida foi baixado, mas não carregou. Envie TurboHunter\runtime\instalacao.log para identificar a causa.' 'Frida was downloaded but could not be loaded. Send TurboHunter\runtime\instalacao.log to identify the cause.')
    }

    # Troca atômica: uma falha de internet nunca apaga os componentes antigos.
    Remove-Item -LiteralPath $PackagesBackupDir -Recurse -Force -ErrorAction SilentlyContinue
    if (Test-Path -LiteralPath $PackagesDir) {
        Move-Item -LiteralPath $PackagesDir -Destination $PackagesBackupDir -Force
    }
    try {
        Move-Item -LiteralPath $PackagesStagingDir -Destination $PackagesDir -Force
    } catch {
        if (-not (Test-Path -LiteralPath $PackagesDir) -and (Test-Path -LiteralPath $PackagesBackupDir)) {
            Move-Item -LiteralPath $PackagesBackupDir -Destination $PackagesDir -Force
        }
        throw
    }
    [System.IO.File]::WriteAllText($PythonPathTemp, $PythonExe, [System.Text.Encoding]::Unicode)
    Move-Item -LiteralPath $PythonPathTemp -Destination $PythonPathFile -Force
    Remove-Item -LiteralPath $PackagesBackupDir -Recurse -Force -ErrorAction SilentlyContinue

    Set-Status 'working' (T 'Etapa 3 de 3 - Finalizando' 'Step 3 of 3 - Finishing') (T 'Criando o iniciador do Turbo Hunter...' 'Creating the Turbo Hunter launcher...') 3
    # Publica o iniciador apenas depois de confirmar Python e Frida.
    $launcherTemp = Join-Path $BaseDir 'INICIAR TURBO HUNTER.vbs.tmp'
    Copy-Item -LiteralPath $StartTemplate -Destination $launcherTemp -Force
    Move-Item -LiteralPath $launcherTemp -Destination $StartLauncher -Force
    [System.IO.File]::WriteAllText($InstallOk, "Turbo Hunter 0.5.4`r`n", [System.Text.Encoding]::Unicode)
    Remove-Item -LiteralPath $PythonInstaller -Force -ErrorAction SilentlyContinue

    # O arquivo CMD continua aberto até a janela de instalação fechar;
    # só o próprio CMD o move como sua última ação.
    try {
        # Ao atualizar um ZIP antigo, retire os atalhos CMD que abrem console.
        foreach ($oldName in @('INSTALAR TURBO HUNTER.vbs', 'INICIAR TURBO HUNTER.cmd')) {
            $oldEntry = Join-Path $BaseDir $oldName
            if (Test-Path -LiteralPath $oldEntry) {
                Move-Item -LiteralPath $oldEntry -Destination (Join-Path $Here $oldName) -Force -ErrorAction Stop
            }
        }
    } catch {
        Write-Log ('Aviso: o arquivo em uso será movido ao fechar o instalador: ' + $_.Exception.Message)
    }

    Set-Status 'done' (T 'Instalação concluída' 'Installation complete') (T 'Abrindo as opções. Na próxima vez, use INICIAR TURBO HUNTER.vbs.' 'Opening settings. Next time, use INICIAR TURBO HUNTER.vbs.') 3
    try {
        $guiFile = Join-Path $InternalDir 'app\TurboHunter.pyw'
        $pythonGui = Join-Path (Split-Path -Parent $PythonExe) 'pythonw.exe'
        if (-not (Test-Path -LiteralPath $guiFile)) { throw 'Arquivo do menu ausente.' }
        if (-not (Test-Path -LiteralPath $pythonGui)) { throw 'pythonw.exe ausente.' }
        $guiProcess = Start-Process -FilePath $pythonGui -ArgumentList ('"' + $guiFile + '"') -PassThru -ErrorAction Stop
        Start-Sleep -Milliseconds 700
        $guiProcess.Refresh()
        if ($guiProcess.HasExited) { throw 'A janela terminou antes de aparecer.' }
        Write-Log (T 'Janela de opções iniciada após instalar.' 'Settings window started after installation.')
    } catch {
        Write-Log ((T 'Abertura direta indisponível: ' 'Direct launch unavailable: ') + $_.Exception.Message)
        try {
            $wscript = Join-Path $env:SystemRoot 'System32\wscript.exe'
            Start-Process -FilePath $wscript -ArgumentList ('"' + $StartLauncher + '"') -ErrorAction Stop | Out-Null
            Write-Log (T 'Abertura alternativa pelo iniciador solicitada.' 'Launcher fallback requested.')
        } catch {
            Write-Log ((T 'Falha ao abrir as opções: ' 'Could not open settings: ') + $_.Exception.Message)
        }
    }
} catch {
    Remove-Item -LiteralPath $PackagesStagingDir -Recurse -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $PythonPathTemp -Force -ErrorAction SilentlyContinue
    if (-not (Test-Path -LiteralPath $PackagesDir) -and (Test-Path -LiteralPath $PackagesBackupDir)) {
        Move-Item -LiteralPath $PackagesBackupDir -Destination $PackagesDir -Force -ErrorAction SilentlyContinue
    }
    Write-Log ('ERROR/ERRO: ' + $_.Exception.ToString())
    if ($HadWorkingInstall -and (Test-Path -LiteralPath $InstallOk)) {
        Write-Log (T 'A instalação anterior foi preservada. Você ainda pode usar o Turbo Hunter e tentar reinstalar depois.' 'The previous installation was preserved. You can still use Turbo Hunter and retry the installation later.')
    } else {
        Write-Log (T 'Nada foi ignorado. Corrija o problema indicado e execute INSTALAR TURBO HUNTER.cmd novamente.' 'Nothing was ignored. Fix the reported problem and run INSTALAR TURBO HUNTER.cmd again.')
    }
    Set-Status 'error' (T 'Não foi possível concluir' 'Could not complete installation') $_.Exception.Message 0
    exit 1
}
