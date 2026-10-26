param([switch]$Confirmed, [int]$WaitForPid = 0, [switch]$Worker,
      [int]$BootstrapPid = 0)
. (Join-Path $PSScriptRoot 'Common.ps1')
Add-Type -AssemblyName System.Windows.Forms
$root = Join-Path $env:LOCALAPPDATA 'TurboHunter'
$mutex = $null
$locked = $false
$exitCode = 0
try {
    Assert-TurboRoot $root
    if (-not $Worker) {
        if (-not $Confirmed) {
            $answer = [Windows.Forms.MessageBox]::Show((T 'Remover o Turbo Hunter, suas preferencias e seus componentes?' 'Remove Turbo Hunter, its settings and its components?'), 'Turbo Hunter 0.6.0', 'YesNo', 'Question')
            if ($answer -ne 'Yes') { exit 0 }
        }
        # The worker, script and working directory must all be OUTSIDE the
        # installation. The original PowerShell and GUI exit before deletion.
        $work = Join-Path ([IO.Path]::GetTempPath()) ('TurboHunter-remove-' + [guid]::NewGuid().ToString('N'))
        if ([IO.Path]::GetFullPath($work).StartsWith(([IO.Path]::GetFullPath($root).TrimEnd('\') + '\'), [StringComparison]::OrdinalIgnoreCase)) {
            throw (T 'Escolha uma pasta temporaria fora do Turbo Hunter e tente novamente.' 'Choose a temporary folder outside Turbo Hunter and try again.')
        }
        New-Item -ItemType Directory -Path $work | Out-Null
        try {
            Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'Common.ps1') -Destination $work
            Copy-Item -LiteralPath $PSCommandPath -Destination (Join-Path $work 'Uninstall.ps1')
            $powershell = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
            $arguments = '-NoProfile -ExecutionPolicy Bypass -File "' + (Join-Path $work 'Uninstall.ps1') + '" -Worker -Confirmed -WaitForPid ' + $WaitForPid + ' -BootstrapPid ' + $PID
            Start-Process -FilePath $powershell -ArgumentList $arguments -WorkingDirectory $work -WindowStyle Hidden | Out-Null
        } catch {
            Remove-Item -LiteralPath $work -Recurse -Force -ErrorAction SilentlyContinue
            throw
        }
    } else {
        # PowerShell's provider location and the process CWD are separate.
        # Release both; neither may keep the installed folder open.
        Set-Location -LiteralPath $PSScriptRoot
        [Environment]::CurrentDirectory = $PSScriptRoot
        if ($BootstrapPid -gt 0) {
            Wait-Process -Id $BootstrapPid -Timeout 15 -ErrorAction SilentlyContinue
            if (Get-Process -Id $BootstrapPid -ErrorAction SilentlyContinue) {
                throw (T 'Feche a janela anterior de desinstalacao e tente novamente.' 'Close the previous uninstall window and try again.')
            }
        }
        $mutex = New-Object Threading.Mutex($false, 'Local\TurboHunterMaintenance060')
        $locked = $mutex.WaitOne(0)
        if (-not $locked) { throw (T 'Outra instalacao esta em andamento. Aguarde e tente novamente.' 'Another installation is running. Wait and try again.') }
        if (Test-Path -LiteralPath $root) {
            [IO.File]::WriteAllText((Join-Path $root '.maintenance'), 'uninstall')
            if ($WaitForPid -gt 0) {
                if (@(Get-TurboProcesses $root | Where-Object {$_.ProcessId -eq $WaitForPid}).Count -gt 0) {
                    Wait-Process -Id $WaitForPid -Timeout 12 -ErrorAction SilentlyContinue
                }
            }
            Stop-TurboProcesses $root
        }
        Remove-LegacyPython $root
        Remove-TurboShortcuts $root
        # File handles may take a moment to close after Python exits.
        for ($attempt = 0; $attempt -lt 8; $attempt++) {
            if (-not (Test-Path -LiteralPath $root)) { break }
            try { Remove-Item -LiteralPath $root -Recurse -Force -ErrorAction Stop } catch {
                if ($attempt -eq 7) { throw }
                Start-Sleep -Milliseconds 500
            }
        }
        if (Test-Path -LiteralPath $root) { throw (T 'Ainda existem arquivos na pasta do mod.' 'Some files remain in the mod folder.') }
        $key = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\TurboHunter'
        if (Test-Path -LiteralPath $key) {
            $entry = Get-ItemProperty -LiteralPath $key
            if ($entry.InstallLocation -and [IO.Path]::GetFullPath([string]$entry.InstallLocation).TrimEnd('\') -ieq [IO.Path]::GetFullPath($root).TrimEnd('\')) {
                Remove-Item -LiteralPath $key -Recurse -Force
                if (Test-Path -LiteralPath $key) { throw (T 'Nao foi possivel remover o registro do Turbo Hunter.' 'Could not remove the Turbo Hunter entry.') }
            }
        }
        [Windows.Forms.MessageBox]::Show((T 'Turbo Hunter removido.' 'Turbo Hunter removed.'), 'Turbo Hunter', 'OK', 'Information') | Out-Null
    }
} catch {
    $exitCode = 1
    $failure = $_.Exception.ToString()
    if (Test-Path -LiteralPath $root) {
        # If another application really holds a file open, keep Windows'
        # Uninstall action usable for the next attempt after partial cleanup.
        if ($Worker -and $locked) {
            try {
                $retryInstaller = Join-Path $root 'TurboHunter\installer'
                Assert-TurboPath $root $retryInstaller
                New-Item -ItemType Directory -Path $retryInstaller -Force | Out-Null
                Copy-Item -LiteralPath $PSCommandPath -Destination (Join-Path $retryInstaller 'Uninstall.ps1') -Force
                Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'Common.ps1') -Destination $retryInstaller -Force
            } catch { }
        }
        try { Add-Content -LiteralPath (Join-Path $root 'uninstall_log.txt') -Encoding UTF8 -Value $failure } catch { }
    }
    $message = if (Test-Path -LiteralPath $root) {
        T 'Nao foi possivel concluir a desinstalacao. Tente novamente.' 'Could not complete uninstallation. Please try again.'
    } else {
        T 'Os arquivos foram removidos, mas nao foi possivel concluir a limpeza do Windows.' 'The files were removed, but Windows cleanup could not be completed.'
    }
    [Windows.Forms.MessageBox]::Show($message, 'Turbo Hunter', 'OK', 'Error') | Out-Null
} finally {
    if ($locked) {
        if (Test-Path -LiteralPath $root) { Remove-Item -LiteralPath (Join-Path $root '.maintenance') -Force -ErrorAction SilentlyContinue }
        $mutex.ReleaseMutex()
    }
    if ($mutex) { $mutex.Dispose() }
    if ($Worker -and [IO.Path]::GetFileName($PSScriptRoot) -match '^TurboHunter-remove-[a-f0-9]{32}$') {
        Set-Location -LiteralPath $env:LOCALAPPDATA
        [Environment]::CurrentDirectory = $env:LOCALAPPDATA
        Remove-Item -LiteralPath $PSScriptRoot -Recurse -Force -ErrorAction SilentlyContinue
    }
}
exit $exitCode
