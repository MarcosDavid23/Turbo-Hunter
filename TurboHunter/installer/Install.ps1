. (Join-Path $PSScriptRoot 'Common.ps1')
$sourceInternal = Split-Path -Parent $PSScriptRoot
$root = Join-Path $env:LOCALAPPDATA 'TurboHunter'
Assert-TurboRoot $root
$mutex = New-Object Threading.Mutex($false, 'Local\TurboHunterMaintenance060')
$locked = $false
try {
    $locked = $mutex.WaitOne(0)
    if (-not $locked) { throw (T 'Outra instalacao esta em andamento. Aguarde e tente novamente.' 'Another installation is running. Wait and try again.') }
    New-Item -ItemType Directory -Force -Path $root | Out-Null
    [IO.File]::WriteAllText((Join-Path $root 'install_log.txt'), '')
    [IO.File]::WriteAllText((Join-Path $root '.maintenance'), 'install')
    # Validate owned subtrees before touching an old runtime or copying files.
    foreach ($relative in @('TurboHunter\runtime', 'TurboHunter\runtime\python-portable', 'TurboHunter\app', 'TurboHunter\installer', 'TurboHunter\assets')) {
        Assert-TurboPath $root (Join-Path $root $relative)
    }
    Write-InstallStatus $root (T 'Preparando o Turbo Hunter 0.6.0...' 'Preparing Turbo Hunter 0.6.0...')
    if (@(Get-TurboProcesses $root).Count -gt 0) {
        Write-InstallStatus $root (T 'Fechando o Turbo Hunter para atualizar...' 'Closing Turbo Hunter for the update...')
    }
    Stop-TurboProcesses $root
    # This is deliberately BEFORE copying/installing the new release. Config
    # is not in the removal list; a shared/system Python is never an owner match.
    Remove-LegacyTurbo $root
    $destination = Join-Path $root 'TurboHunter'
    if ([IO.Path]::GetFullPath($sourceInternal).TrimEnd('\') -ine [IO.Path]::GetFullPath($destination).TrimEnd('\')) {
        Write-InstallStatus $root (T 'Instalando os arquivos do mod...' 'Installing the mod files...')
        # Mirror shipped files so obsolete scripts from ANY older mod version
        # disappear too. The private runtime and player preferences are excluded.
        & robocopy.exe $sourceInternal $destination /MIR /XD runtime payload /XF hud_config.json /XJ /NFL /NDL /NJH /NJS /NP /R:2 /W:1 | Out-Null
        if ($LASTEXITCODE -ge 8) { throw (T 'Nao foi possivel copiar os arquivos. Feche o mod e tente novamente.' 'Could not copy the files. Close the mod and try again.') }
        $config = Join-Path $destination 'app\hud_config.json'
        if (-not (Test-Path -LiteralPath $config)) {
            Copy-Item -LiteralPath (Join-Path $sourceInternal 'app\hud_config.json') -Destination $config
        }
    }
    & (Join-Path $destination 'installer\InstallerWorker.ps1') -PayloadDir (Join-Path $PSScriptRoot 'payload')
    if ($LASTEXITCODE -ne 0) { exit 1 }
    exit 0
} catch {
    if ($locked -and (Test-Path -LiteralPath $root)) {
        Add-Content -LiteralPath (Join-Path $root 'install_log.txt') -Encoding UTF8 -Value $_.Exception.ToString()
    }
    Write-Host (T 'Nao foi possivel concluir a instalacao.' 'Could not complete installation.')
    Write-Host $_.Exception.Message
    exit 1
} finally {
    if ($locked) {
        Remove-Item -LiteralPath (Join-Path $root '.maintenance') -Force -ErrorAction SilentlyContinue
        $mutex.ReleaseMutex()
    }
    $mutex.Dispose()
}
