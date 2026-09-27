# Turbo Hunter 0.5.7 - installer worker
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
    $pythonw = Join-Path (Split-Path -Parent $Exe) 'pythonw.exe'
    if (-not (Test-Path -LiteralPath $pythonw)) { return $false }
    try {
        & $Exe -c "import sys, tkinter; raise SystemExit(0 if sys.version_info >= (3, 9) and sys.maxsize > 2**32 else 2)" *> $null
        return ($LASTEXITCODE -eq 0)
    } catch { return $false }
}

function Get-RegisteredPython {
    # O instalador oficial registra Python por usuario, mesmo quando o
    # executavel nao esta no PATH nem existe um comando py.exe.
    $roots = @('HKCU:\Software\Python\PythonCore',
               'HKLM:\Software\Python\PythonCore',
               'HKLM:\Software\WOW6432Node\Python\PythonCore')
    foreach ($root in $roots) {
        if (-not (Test-Path -LiteralPath $root)) { continue }
        foreach ($version in (Get-ChildItem -LiteralPath $root -ErrorAction SilentlyContinue)) {
            $installKey = Join-Path $version.PSPath 'InstallPath'
            if (-not (Test-Path -LiteralPath $installKey)) { continue }
            try {
                $entry = Get-Item -LiteralPath $installKey -ErrorAction Stop
                $location = [string]$entry.GetValue('')
                $explicitExe = [string]$entry.GetValue('ExecutablePath')
                if (-not $location -and $explicitExe) {
                    $location = Split-Path -Parent $explicitExe
                }
                if (-not $location) { continue }
                $location = [Environment]::ExpandEnvironmentVariables($location.Trim(' ', '"'))
                $exe = if ($explicitExe) {
                    [Environment]::ExpandEnvironmentVariables($explicitExe.Trim(' ', '"'))
                } else { Join-Path $location 'python.exe' }
                [pscustomobject]@{
                    Exe=$exe; Location=$location; Tag=$version.PSChildName;
                    Registry=$root
                }
            } catch {
                Write-Log ('Aviso: registro Python ignorado: ' + $_.Exception.Message)
            }
        }
    }
}

function Test-TurboPrivatePath([string]$Location) {
    return $Location -match '(?i)[\\/]TurboHunter[\\/]runtime[\\/]python[\\/]*$'
}

function Find-Python {
    if (Test-Path -LiteralPath $PythonPathFile) {
        $saved = (Get-Content -LiteralPath $PythonPathFile -Raw -ErrorAction SilentlyContinue).Trim()
        if (Test-PythonExe $saved) {
            Write-Log ('Reutilizando Python salvo: ' + $saved)
            return $saved
        }
    }

    # Primeiro reutilize a instalacao estavel da versao atual.
    $private = Join-Path $PrivatePythonDir 'python.exe'
    if (Test-PythonExe $private) {
        Write-Log ('Reutilizando Python privado: ' + $private)
        return $private
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

    foreach ($registered in (Get-RegisteredPython)) {
        if (Test-PythonExe $registered.Exe) {
            Write-Log ('Python existente encontrado no registro: ' + $registered.Exe)
            return $registered.Exe
        }
    }
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
    # Um reparo incompleto nunca deve aparentar que a instalação terminou.
    Remove-Item -LiteralPath $InstallOk -Force -ErrorAction SilentlyContinue
    Set-Status 'working' (T 'Etapa 1 de 3 - Python' 'Step 1 of 3 - Python') (T 'Procurando uma instalação compatível do Python 3...' 'Looking for a compatible Python 3 installation...') 1

    $PythonExe = Find-Python
    if (-not $PythonExe) {
        $registered314 = @(Get-RegisteredPython | Where-Object {
            $_.Registry -eq 'HKCU:\Software\Python\PythonCore' -and
            $_.Tag -match '^3\.14(-64)?$'
        })
        $brokenPrivate = @($registered314 | Where-Object {
            (Test-TurboPrivatePath $_.Location) -and
            -not (Test-PythonExe $_.Exe)
        })
        # Um Python de outro programa nunca e removido pelo Turbo Hunter.
        $foreign = @($registered314 | Where-Object {
            -not (Test-TurboPrivatePath $_.Location)
        })
        if ($foreign.Count -gt 0 -and $brokenPrivate.Count -eq 0) {
            Write-Log ('Python 3.14 registrado fora do Turbo Hunter: ' +
                ($foreign[0].Location))
            throw (T 'Python 3.14 de outro programa parece incompleto. Repare essa instalação pelo Windows; o Turbo Hunter não a removerá.' 'Python 3.14 belonging to another app seems incomplete. Repair it through Windows; Turbo Hunter will not remove it.')
        }
        if ($brokenPrivate.Count -gt 1) {
            throw (T 'Mais de uma cópia privada antiga encontrada. Envie o log antes de reparar.' 'Multiple old private copies found. Send the log before repairing.')
        }
        Set-Status 'working' (T 'Etapa 1 de 3 - Python' 'Step 1 of 3 - Python') (T 'Baixando Python 3.14.7 oficial. Aguarde...' 'Downloading official Python 3.14.7. Please wait...') 1
        if (Test-Path -LiteralPath $PythonInstaller) { Remove-Item -LiteralPath $PythonInstaller -Force -ErrorAction SilentlyContinue }
        $wc = New-Object System.Net.WebClient
        $wc.Headers.Add('User-Agent', 'TurboHunter/0.5.7')
        $wc.DownloadFile($PythonUrl, $PythonInstaller)
        $hash = (Get-FileHash -Algorithm SHA256 -LiteralPath $PythonInstaller).Hash.ToLowerInvariant()
        if ($hash -ne $PythonSha256) {
            throw (T 'A verificação de segurança do instalador do Python falhou.' 'The Python installer security verification failed.')
        }

        if ($brokenPrivate.Count -eq 1) {
            $old = $brokenPrivate[0]
            $repairDetail = (T 'Cópia privada antiga incompleta em ' 'Old private copy incomplete at ') +
                $old.Location + (T '. Removendo só essa cópia antes de instalar.' '. Removing only this copy before installing.')
            Set-Status 'working' (T 'Etapa 1 de 3 - Python' 'Step 1 of 3 - Python') $repairDetail 1
            $uninstallLog = Join-Path $RuntimeDir 'python_antigo_desinstalacao.log'
            $uninstallArgs = '/uninstall /quiet /log "' + $uninstallLog + '"'
            $uninstall = Start-Process -FilePath $PythonInstaller -ArgumentList $uninstallArgs -PassThru -Wait -WindowStyle Hidden
            Write-Log ('Desinstalação da cópia privada terminou com código ' + $uninstall.ExitCode + '.')
            if ($uninstall.ExitCode -ne 0) {
                throw (T 'Não foi possível remover o Python privado anterior. Consulte python_antigo_desinstalacao.log; nenhum outro Python foi removido.' 'Could not remove the previous private Python. See python_antigo_desinstalacao.log; no other Python was removed.')
            }
        }

        Set-Status 'working' (T 'Etapa 1 de 3 - Python' 'Step 1 of 3 - Python') (T 'Instalando uma cópia privada do Python para o Turbo Hunter...' 'Installing a private Python copy for Turbo Hunter...') 1
        if (Test-Path -LiteralPath $PrivatePythonDir) { Remove-Item -LiteralPath $PrivatePythonDir -Recurse -Force -ErrorAction SilentlyContinue }
        New-Item -ItemType Directory -Force -Path $PrivatePythonDir | Out-Null
        $arguments = '/quiet InstallAllUsers=0 TargetDir="' + $PrivatePythonDir + '" PrependPath=0 AppendPath=0 Include_launcher=0 Include_pip=1 Include_tcltk=1 Include_test=0 Include_doc=0 Shortcuts=0 AssociateFiles=0'
        $pythonInstallLog = Join-Path $RuntimeDir 'python_instalacao.log'
        $arguments += ' /log "' + $pythonInstallLog + '"'
        $proc = Start-Process -FilePath $PythonInstaller -ArgumentList $arguments -PassThru -Wait -WindowStyle Hidden
        Write-Log ('Instalador do Python terminou com código ' + $proc.ExitCode + '.')
        if ($proc.ExitCode -ne 0) {
            throw ((T 'A instalação do Python terminou com código ' 'Python installation ended with code ') + $proc.ExitCode + '.')
        }
        $PythonExe = Join-Path $PrivatePythonDir 'python.exe'
        if (-not (Test-PythonExe $PythonExe)) {
            throw (T 'O Python não passou na verificação final. Consulte python_instalacao.log; nenhum Python de outro programa foi removido.' 'Python failed final verification. See python_instalacao.log; no other app Python was removed.')
        }
    }

    Write-Log ('Python selecionado para esta instalação: ' + $PythonExe)
    Set-Status 'working' (T 'Etapa 2 de 3 - Componentes' 'Step 2 of 3 - Components') (T 'Python pronto. Preparando o Frida...' 'Python is ready. Preparing Frida...') 2
    [System.IO.File]::WriteAllText($PythonPathFile, $PythonExe, [System.Text.Encoding]::Unicode)
    Ensure-Pip $PythonExe

    if (Test-Path -LiteralPath $PackagesDir) { Remove-Item -LiteralPath $PackagesDir -Recurse -Force -ErrorAction SilentlyContinue }
    New-Item -ItemType Directory -Force -Path $PackagesDir | Out-Null
    Set-Status 'working' (T 'Etapa 2 de 3 - Componentes' 'Step 2 of 3 - Components') ((T 'Baixando e instalando Frida ' 'Downloading and installing Frida ') + $FridaVersion + '...') 2
    $installExit = Invoke-Logged $PythonExe @('-m', 'pip', 'install', '--disable-pip-version-check', '--no-input', '--upgrade', '--target', $PackagesDir, "frida==$FridaVersion")
    if ($installExit -ne 0) {
        throw (T 'Não foi possível baixar ou instalar o Frida. Verifique a internet ou o bloqueio da rede.' 'Frida could not be downloaded or installed. Check your internet connection or network restrictions.')
    }

    $oldPythonPath = $env:PYTHONPATH
    try {
        $env:PYTHONPATH = $PackagesDir
        # Verificar a funcao realmente usada pelo mod, sem depender de
        # frida.__version__, que nao e necessaria para executar o programa.
        $verifyExit = Invoke-Logged $PythonExe @('-c', 'import frida, tkinter; assert callable(frida.attach); print(frida.__file__)')
    } finally {
        $env:PYTHONPATH = $oldPythonPath
    }
    if ($verifyExit -ne 0) {
        throw (T 'O Frida foi baixado, mas não carregou. Envie TurboHunter\runtime\instalacao.log para identificar a causa.' 'Frida was downloaded but could not be loaded. Send TurboHunter\runtime\instalacao.log to identify the cause.')
    }

    Set-Status 'working' (T 'Etapa 3 de 3 - Finalizando' 'Step 3 of 3 - Finishing') (T 'Criando o iniciador do Turbo Hunter...' 'Creating the Turbo Hunter launcher...') 3
    # Publica o iniciador apenas depois de confirmar Python e Frida.
    $launcherTemp = Join-Path $BaseDir 'INICIAR TURBO HUNTER.vbs.tmp'
    Copy-Item -LiteralPath $StartTemplate -Destination $launcherTemp -Force
    Move-Item -LiteralPath $launcherTemp -Destination $StartLauncher -Force
    Remove-Item -LiteralPath $PythonInstaller -Force -ErrorAction SilentlyContinue

    # O atalho abre o mod em --autostart e em seguida inicia o jogo.
    $togetherScript = Join-Path $Here 'LaunchTogether.ps1'
    if (-not (Test-Path -LiteralPath $togetherScript)) { throw 'Iniciador mod + jogo ausente.' }
    $desktop = [Environment]::GetFolderPath('DesktopDirectory')
    if (-not $desktop) { throw 'Area de Trabalho nao encontrada.' }
    $shortcutPath = Join-Path $desktop 'Turbo Hunter + theHunter.lnk'
    $shortcut = (New-Object -ComObject WScript.Shell).CreateShortcut($shortcutPath)
    $shortcut.TargetPath = (Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe')
    $shortcut.Arguments = '-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "' + $togetherScript + '"'
    $shortcut.WorkingDirectory = $BaseDir
    $shortcut.IconLocation = Join-Path $InternalDir 'assets\turbo_hunter.ico'
    $shortcut.Description = 'Inicia Turbo Hunter e theHunter: Call of the Wild'
    $shortcut.WindowStyle = 7
    $shortcut.Save()
    Write-Log ('Atalho criado: ' + $shortcutPath)
    [System.IO.File]::WriteAllText($InstallOk, "Turbo Hunter 0.5.7`r`n", [System.Text.Encoding]::Unicode)

    # Atalhos antigos que abriam console ficam fora da pasta principal.
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

    Set-Status 'done' (T 'Instalação concluída' 'Installation complete') (T 'Abrindo as opções. Na próxima vez, use START TURBO HUNTER.cmd.' 'Opening settings. Next time, use START TURBO HUNTER.cmd.') 3
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
    Write-Log ('ERROR/ERRO: ' + $_.Exception.ToString())
    Set-Status 'error' (T 'Não foi possível concluir' 'Could not complete installation') $_.Exception.Message 0
    exit 1
}
