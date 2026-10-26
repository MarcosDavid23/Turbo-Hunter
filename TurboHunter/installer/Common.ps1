# Shared lifecycle functions. Only Turbo Hunter-owned paths are modified.
$ErrorActionPreference = 'Stop'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
function T([string]$Pt, [string]$En) {
    if ([Globalization.CultureInfo]::CurrentUICulture.Name.StartsWith('pt', [StringComparison]::OrdinalIgnoreCase)) { return $Pt }
    return $En
}
function Write-InstallStatus([string]$Root, [string]$Text) {
    Add-Content -LiteralPath (Join-Path $Root 'install_log.txt') -Encoding UTF8 -Value ('[' + (Get-Date -Format 'yyyy-MM-dd HH:mm:ss') + '] ' + $Text)
    Write-Host $Text
}
function Assert-TurboRoot([string]$Root) {
    $expected = [IO.Path]::GetFullPath((Join-Path $env:LOCALAPPDATA 'TurboHunter')).TrimEnd('\')
    $actual = [IO.Path]::GetFullPath($Root).TrimEnd('\')
    if ($actual -ine $expected) { throw 'Pasta de instalacao invalida.' }
    if ((Test-Path -LiteralPath $Root) -and ((Get-Item -LiteralPath $Root).Attributes -band [IO.FileAttributes]::ReparsePoint)) {
        throw 'A pasta do Turbo Hunter e um redirecionamento. Nenhum arquivo foi removido.'
    }
}
function Assert-TurboPath([string]$Root, [string]$Path) {
    Assert-TurboRoot $Root
    $base = [IO.Path]::GetFullPath($Root).TrimEnd('\')
    $target = [IO.Path]::GetFullPath($Path).TrimEnd('\')
    if (-not $target.StartsWith($base + '\', [StringComparison]::OrdinalIgnoreCase)) {
        throw (T 'A pasta do componente antigo nao pertence ao Turbo Hunter.' 'The old component folder does not belong to Turbo Hunter.')
    }
    $current = $target
    while ($current.Length -gt $base.Length) {
        if (Test-Path -LiteralPath $current) {
            if ((Get-Item -LiteralPath $current -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) {
                throw (T 'Uma pasta do mod foi redirecionada. Restaure a pasta antes de instalar.' 'A mod folder was redirected. Restore the folder before installing.')
            }
        }
        $current = Split-Path -Parent $current
    }
}
function Get-TurboProcesses([string]$Root) {
    $runtimePrefix = (Join-Path $Root 'TurboHunter\runtime') + '\'
    $appPrefix = Join-Path $Root 'TurboHunter\app\'
    @(Get-CimInstance Win32_Process -ErrorAction Stop | Where-Object {
        $_.ProcessId -ne $PID -and (
            ($_.ExecutablePath -and $_.ExecutablePath.StartsWith($runtimePrefix, [StringComparison]::OrdinalIgnoreCase)) -or
            ($_.Name -match '^pythonw?\.exe$' -and $_.CommandLine -and $_.CommandLine.IndexOf($appPrefix, [StringComparison]::OrdinalIgnoreCase) -ge 0)
        )
    })
}
function Stop-TurboProcesses([string]$Root) {
    $stopFile = Join-Path $Root 'TurboHunter\app\.turbo_hunter_stop'
    if (Test-Path -LiteralPath (Split-Path -Parent $stopFile)) { [IO.File]::WriteAllText($stopFile, 'stop') }
    $deadline = (Get-Date).AddSeconds(10)
    do {
        $processes = @(Get-TurboProcesses $Root)
        if ($processes.Count -eq 0) { return }
        Start-Sleep -Milliseconds 250
    } while ((Get-Date) -lt $deadline)
    foreach ($process in $processes) {
        Stop-Process -Id $process.ProcessId -Force -ErrorAction SilentlyContinue
    }
    Start-Sleep -Milliseconds 400
    if (@(Get-TurboProcesses $Root).Count -gt 0) { throw 'Feche o Turbo Hunter antes de continuar.' }
}
function Download-Verified([string]$Url, [string]$Destination, [string]$Hash,
                           [long]$SizeBytes = 0, [string]$Label = 'Componente') {
    # Use plain CMD lines; Write-Progress alone can
    # disappear when PowerShell is launched by a .cmd file or output is captured.
    $partial = $Destination + '.part'
    for ($attempt = 1; $attempt -le 3; $attempt++) {
        $client = New-Object Net.WebClient
        $task = $null
        try {
            Write-Host ((T 'Baixando {0}: 0% - conectando (tentativa {1}/3)...' 'Downloading {0}: 0% - connecting (attempt {1}/3)...') -f $Label, $attempt)
            $client.Headers.Add('User-Agent', 'TurboHunter/0.6.0')
            $task = $client.DownloadFileTaskAsync([Uri]$Url, $partial)
            $clock = [Diagnostics.Stopwatch]::StartNew()
            [long]$previousBytes = 0
            $lastData = 0.0
            $lastDisplay = -1.0
            while (-not $task.IsCompleted) {
                [long]$received = 0
                if (Test-Path -LiteralPath $partial) {
                    $received = (Get-Item -LiteralPath $partial).Length
                }
                if ($received -gt $previousBytes) {
                    $lastData = $clock.Elapsed.TotalSeconds
                    $previousBytes = $received
                }
                if (($clock.Elapsed.TotalSeconds - $lastDisplay) -ge 1) {
                    if ($SizeBytes -gt 0) {
                        $percent = [Math]::Min(99, [Math]::Floor(100.0 * $received / $SizeBytes))
                        $line = ((T 'Baixando {0}: {1}% - {2:N1} / {3:N1} MB' 'Downloading {0}: {1}% - {2:N1} / {3:N1} MB') -f $Label, $percent, ($received / 1000000.0), ($SizeBytes / 1000000.0))
                    } else {
                        $line = ((T 'Baixando {0}: {1:N1} MB recebidos' 'Downloading {0}: {1:N1} MB received') -f $Label, ($received / 1000000.0))
                    }
                    Write-Host $line
                    $lastDisplay = $clock.Elapsed.TotalSeconds
                }
                if (($clock.Elapsed.TotalSeconds - $lastData) -ge 60) {
                    throw 'Servidor ou conexao sem enviar dados por 60 segundos.'
                }
                Start-Sleep -Milliseconds 200
            }
            # A completed task may have failed: propagate that failure before
            # displaying 100% or verifying the downloaded file.
            $task.GetAwaiter().GetResult()
            Write-Host ((T 'Baixando {0}: 100% - download concluido.' 'Downloading {0}: 100% - download complete.') -f $Label)
            if ($Hash) {
                Write-Host ((T 'Conferindo {0}...' 'Checking {0}...') -f $Label)
                if ((Get-FileHash -LiteralPath $partial -Algorithm SHA256).Hash -ine $Hash) {
                    throw (T 'O download esta incompleto. Tente novamente.' 'The download is incomplete. Please try again.')
                }
            }
            Move-Item -LiteralPath $partial -Destination $Destination -Force
            return
        } catch {
            $reason = $_.Exception.GetBaseException().Message
            Write-Host ((T 'O download de {0} foi interrompido.' 'The {0} download was interrupted.') -f $Label)
            if ($attempt -eq 3) {
                throw ('Nao foi possivel baixar {0} apos 3 tentativas. Verifique a internet e o acesso ao servidor. Detalhe: {1}' -f $Label, $reason)
            }
            Write-Host (T 'Tentando baixar novamente...' 'Retrying the download...')
        } finally {
            $client.CancelAsync()
            if ($task -and -not $task.IsCompleted) {
                try { [void]$task.Wait(3000) } catch { }
            }
            $client.Dispose()
            Remove-Item -LiteralPath $partial -Force -ErrorAction SilentlyContinue
        }
        Start-Sleep -Seconds 2
    }
}
function Get-LegacyPythonRecords([string]$Root) {
    # Never infer ownership from a Python version or from python_path.txt:
    # 0.5.7 could also reuse Python belonging to the user or another program.
    $privatePath = [IO.Path]::GetFullPath((Join-Path $Root 'TurboHunter\runtime\python')).TrimEnd('\')
    Assert-TurboPath $Root $privatePath
    $registryRoot = 'HKCU:\Software\Python\PythonCore'
    if (-not (Test-Path -LiteralPath $registryRoot)) { return }
    foreach ($version in @(Get-ChildItem -LiteralPath $registryRoot)) {
        $key = Join-Path $version.PSPath 'InstallPath'
        if (-not (Test-Path -LiteralPath $key)) { continue }
        $location = [string](Get-Item -LiteralPath $key).GetValue('')
        if (-not $location) { continue }
        $location = [Environment]::ExpandEnvironmentVariables($location.Trim(' ', '"'))
        if ([IO.Path]::GetFullPath($location).TrimEnd('\') -ine $privatePath) { continue }
        [pscustomobject]@{ InstallKey=$key; Tag=($version.PSChildName -replace '-64$', ''); Location=$privatePath }
    }
}
function Remove-LegacyPython([string]$Root) {
    # Official uninstall FIRST, file cleanup second. Deleting a registered
    # private Python tree directly would leave Windows Installer broken.
    foreach ($record in @(Get-LegacyPythonRecords $Root)) {
        $tag = $record.Tag
        if ($tag -notmatch '^3\.\d+$') { throw (T 'Nao foi possivel identificar o Python antigo do mod.' 'Could not identify the old mod Python.') }
        $uninstallRoot = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall'
        $entries = @(Get-ChildItem -LiteralPath $uninstallRoot -ErrorAction SilentlyContinue | ForEach-Object {
            Get-ItemProperty -LiteralPath $_.PSPath
        } | Where-Object { $_.DisplayName -match ('^Python ' + [regex]::Escape($tag) + '\.\d+ \(64-bit\)$') })
        if ($entries.Count -ne 1) { throw (T 'Repare a copia antiga do Python pelo Windows e execute o instalador novamente.' 'Repair the old Python copy through Windows, then run the installer again.') }
        $display = [string]$entries[0].DisplayName
        if ($display -notmatch '^Python (3\.\d+\.\d+) \(64-bit\)$') { throw (T 'Nao foi possivel identificar o Python antigo do mod.' 'Could not identify the old mod Python.') }
        $pythonVersion = $Matches[1]
        if (Test-Path -LiteralPath (Join-Path $Root 'install_log.txt')) {
            Write-InstallStatus $Root (T 'Removendo o Python da versao anterior...' 'Removing Python from the previous version...')
        }
        $work = Join-Path ([IO.Path]::GetTempPath()) ('TurboHunter-cleanup-' + [guid]::NewGuid().ToString('N'))
        New-Item -ItemType Directory -Path $work | Out-Null
        try {
            $exe = Join-Path $work 'python-uninstall.exe'
            $cached = [string]$entries[0].UninstallString
            $match = [regex]::Match($cached, '^\s*"?(.+?\.exe)"?(?:\s|$)')
            if ($match.Success -and (Test-Path -LiteralPath $match.Groups[1].Value)) {
                Copy-Item -LiteralPath $match.Groups[1].Value -Destination $exe
            } else {
                Write-Host (T 'Preparando a remocao dos componentes antigos...' 'Preparing to remove the old components...')
                $url = 'https://www.python.org/ftp/python/' + $pythonVersion + '/python-' + $pythonVersion + '-amd64.exe'
                Download-Verified -Url $url -Destination $exe -Hash '' -Label (T 'desinstalador do Python antigo' 'old Python uninstaller')
            }
            $signature = Get-AuthenticodeSignature -LiteralPath $exe
            if ($signature.Status -ne 'Valid' -or $signature.SignerCertificate.Subject -notmatch 'Python Software Foundation') {
                throw (T 'Nao foi possivel verificar o desinstalador do Python antigo. Tente novamente.' 'Could not verify the old Python uninstaller. Please try again.')
            }
            $process = Start-Process -FilePath $exe -ArgumentList '/uninstall /quiet /norestart' -Wait -PassThru
            if ($process.ExitCode -notin @(0, 3010)) { throw (T 'Nao foi possivel remover o Python antigo. Reinicie o Windows e tente novamente.' 'Could not remove the old Python. Restart Windows and try again.') }
            if (@(Get-LegacyPythonRecords $Root | Where-Object { $_.InstallKey -eq $record.InstallKey }).Count -gt 0) {
                throw (T 'O Windows ainda precisa concluir a remocao do Python antigo. Reinicie e tente novamente.' 'Windows still needs to finish removing the old Python. Restart and try again.')
            }
        } finally { Remove-Item -LiteralPath $work -Recurse -Force -ErrorAction SilentlyContinue }
    }
}
function Get-LegacyTurboPaths([string]$Root) {
    $relativePaths = @(
        'Laboratorio', 'TurboHunter\runtime\python', 'TurboHunter\runtime\packages',
        'TurboHunter\app\__pycache__',
        'TurboHunter\app\turbo_hunter_log.txt', 'TurboHunter\app\turbo_hunter_log_anterior.txt',
        'TurboHunter\app\lab_id_vivo_live.jsonl', 'TurboHunter\app\lab_id_vivo_state.json',
        'TurboHunter\runtime\instalacao.log', 'TurboHunter\runtime\launcher.log',
        'TurboHunter\runtime\install_status.json', 'TurboHunter\runtime\__pycache__',
        'TurboHunter\runtime\pip-cache',
        'INSTALAR TURBO HUNTER.vbs', 'INICIAR TURBO HUNTER.cmd',
        'TurboHunter\installer\INSTALAR TURBO HUNTER.cmd',
        'TurboHunter\installer\INSTALAR TURBO HUNTER.vbs',
        'TurboHunter\installer\INICIAR TURBO HUNTER.cmd'
    )
    foreach ($relative in $relativePaths) {
        $path = Join-Path $Root $relative
        Assert-TurboPath $Root $path
        if (Test-Path -LiteralPath $path) { $path }
    }
    foreach ($directory in @($Root, (Join-Path $Root 'TurboHunter\runtime'))) {
        if ($directory -ne $Root) { Assert-TurboPath $Root $directory }
        if (-not (Test-Path -LiteralPath $directory)) { continue }
        $patterns = if ($directory -eq $Root) { @('TurboHunter_Log_*.zip') } else { @('python-*.exe', 'python_*.log') }
        foreach ($pattern in $patterns) {
            foreach ($file in @(Get-ChildItem -LiteralPath $directory -Filter $pattern -File)) {
                Assert-TurboPath $Root $file.FullName
                $file.FullName
            }
        }
        if ($directory -ne $Root) {
            foreach ($staleStage in @(Get-ChildItem -LiteralPath $directory -Filter 'stage-*' -Directory)) {
                Assert-TurboPath $Root $staleStage.FullName
                $staleStage.FullName
            }
        }
    }
}
function Remove-LegacyTurbo([string]$Root) {
    $paths = @(Get-LegacyTurboPaths $Root)
    $records = @(Get-LegacyPythonRecords $Root)
    if ($paths.Count -eq 0 -and $records.Count -eq 0) { return }
    Write-InstallStatus $Root (T 'Removendo os componentes da versao anterior...' 'Removing components from the previous version...')
    Remove-LegacyPython $Root
    foreach ($path in $paths) {
        Assert-TurboPath $Root $path
        if (Test-Path -LiteralPath $path) { Remove-Item -LiteralPath $path -Recurse -Force }
    }
    Remove-TurboShortcuts $Root
    foreach ($name in @('python_path.txt', 'install_ok.txt')) {
        $path = Join-Path $Root ('TurboHunter\runtime\' + $name)
        Assert-TurboPath $Root $path
        if (Test-Path -LiteralPath $path) { Remove-Item -LiteralPath $path -Force }
    }
    # Only this app's entry, and only if it points at our exact installation.
    $key = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\TurboHunter'
    if (Test-Path -LiteralPath $key) {
        $entry = Get-ItemProperty -LiteralPath $key
        if ($entry.InstallLocation -and [IO.Path]::GetFullPath([string]$entry.InstallLocation).TrimEnd('\') -ieq [IO.Path]::GetFullPath($Root).TrimEnd('\')) {
            Remove-Item -LiteralPath $key -Recurse -Force
        }
    }
    Write-InstallStatus $Root (T 'Componentes antigos removidos.' 'Previous components removed.')
}
function Remove-TurboShortcuts([string]$Root) {
    $shell = New-Object -ComObject WScript.Shell
    $names = @('Turbo Hunter.lnk', 'Turbo Hunter + theHunter.lnk', 'TurboHunter.lnk')
    $paths = @()
    foreach ($folder in @([Environment]::GetFolderPath('DesktopDirectory'), [Environment]::GetFolderPath('Programs'))) {
        foreach ($name in $names) { $paths += Join-Path $folder $name }
    }
    # Remember where shortcuts were created, including a Desktop later moved
    # into/out of OneDrive. Still verify their target before deleting anything.
    $record = Join-Path $Root 'TurboHunter\runtime\ownership.json'
    if (Test-Path -LiteralPath $record) {
        try {
            $info = Get-Content -LiteralPath $record -Raw | ConvertFrom-Json
            foreach ($path in $info.shortcuts) {
                if ([IO.Path]::GetFileName([string]$path) -in $names) { $paths += [string]$path }
            }
        } catch { }
    }
    foreach ($path in @($paths | Select-Object -Unique)) {
        if (-not (Test-Path -LiteralPath $path)) { continue }
        $shortcut = $shell.CreateShortcut($path)
        $target = [Environment]::ExpandEnvironmentVariables($shortcut.TargetPath + ' ' + $shortcut.Arguments)
        if ($target.IndexOf(($Root.TrimEnd('\') + '\'), [StringComparison]::OrdinalIgnoreCase) -ge 0) { Remove-Item -LiteralPath $path -Force }
    }
}
