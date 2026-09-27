$ErrorActionPreference = 'Stop'
$Here = Split-Path -Parent $MyInvocation.MyCommand.Path
$Internal = Split-Path -Parent $Here
$Base = Split-Path -Parent $Internal
$Runtime = Join-Path $Internal 'runtime'
$GameChoiceFile = Join-Path $Runtime 'game_platform.txt'
$GamePathFile = Join-Path $Runtime 'game_path.txt'
$LogFile = Join-Path $Runtime 'launcher.log'
New-Item -ItemType Directory -Force -Path $Runtime | Out-Null
function Write-LauncherLog([string]$Message) {
    Add-Content -LiteralPath $LogFile -Encoding UTF8 -Value ('[' + (Get-Date -Format 'yyyy-MM-dd HH:mm:ss') + '] ' + $Message)
}
try {
    $Python = (Get-Content -LiteralPath (Join-Path $Runtime 'python_path.txt') -Raw).Trim()
    $PythonW = Join-Path (Split-Path -Parent $Python) 'pythonw.exe'
    $Gui = Join-Path $Internal 'app\TurboHunter.pyw'
    if (-not (Test-Path -LiteralPath $PythonW) -or -not (Test-Path -LiteralPath $Gui)) {
        throw 'Turbo Hunter nao foi instalado. Execute INSTALL TURBO HUNTER.cmd no ZIP extraido.'
    }
    Start-Process -FilePath $PythonW -ArgumentList ('"' + $Gui + '" --autostart') -WorkingDirectory $Base
    Write-LauncherLog 'Turbo Hunter iniciado em modo automatico.'

    if (Get-Process -Name 'theHunterCotW_F' -ErrorAction SilentlyContinue) {
        Write-LauncherLog 'O jogo ja esta aberto.'
        exit 0
    }

    $SteamInstalled = $false
    $SteamRoots = New-Object System.Collections.Generic.List[string]
    foreach ($Root in @((Join-Path ${env:ProgramFiles(x86)} 'Steam'), (Join-Path $env:ProgramFiles 'Steam'))) {
        if ($Root -and (Test-Path -LiteralPath $Root)) { $SteamRoots.Add($Root) }
    }
    try {
        $SteamReg = (Get-ItemProperty -Path 'HKCU:\Software\Valve\Steam' -Name SteamPath -ErrorAction Stop).SteamPath
        if ($SteamReg -and (Test-Path -LiteralPath $SteamReg)) { $SteamRoots.Add($SteamReg) }
    } catch {}
    foreach ($Root in $SteamRoots) {
        if (Test-Path -LiteralPath (Join-Path $Root 'steamapps\appmanifest_518790.acf')) { $SteamInstalled = $true; break }
        $LibrariesFile = Join-Path $Root 'steamapps\libraryfolders.vdf'
        if (Test-Path -LiteralPath $LibrariesFile) {
            $Libraries = [regex]::Matches((Get-Content -LiteralPath $LibrariesFile -Raw), '"path"\s+"([^"]+)"')
            foreach ($Library in $Libraries) {
                $LibraryRoot = $Library.Groups[1].Value.Replace('\\', '\')
                if (Test-Path -LiteralPath (Join-Path $LibraryRoot 'steamapps\appmanifest_518790.acf')) {
                    $SteamInstalled = $true; break
                }
            }
        }
        if ($SteamInstalled) { break }
    }

    $EpicApp = ''
    $ManifestDir = Join-Path $env:ProgramData 'Epic\EpicGamesLauncher\Data\Manifests'
    if (Test-Path -LiteralPath $ManifestDir) {
        foreach ($Manifest in (Get-ChildItem -LiteralPath $ManifestDir -Filter '*.item' -File -ErrorAction SilentlyContinue)) {
            try {
                $Info = Get-Content -LiteralPath $Manifest.FullName -Raw | ConvertFrom-Json
                if ([string]$Info.DisplayName -match '^theHunter:\s*Call of the Wild' -and
                    -not [string]::IsNullOrWhiteSpace([string]$Info.AppName)) {
                    $EpicApp = [string]$Info.AppName; break
                }
            } catch {}
        }
    }

    $Choice = ''
    if (Test-Path -LiteralPath $GameChoiceFile) { $Choice = (Get-Content -LiteralPath $GameChoiceFile -Raw).Trim().ToLowerInvariant() }
    if ($Choice -eq 'steam' -and -not $SteamInstalled) { $Choice = '' }
    if ($Choice -eq 'epic' -and -not $EpicApp) { $Choice = '' }
    if ($Choice -eq 'custom' -and -not (Test-Path -LiteralPath $GamePathFile)) { $Choice = '' }
    if (-not $Choice) {
        if ($SteamInstalled -and $EpicApp) {
            Add-Type -AssemblyName System.Windows.Forms
            $Answer = [System.Windows.Forms.MessageBox]::Show('Onde voce joga theHunter? Sim = Steam; Nao = Epic Games.', 'Turbo Hunter + theHunter', 'YesNo', 'Question')
            $Choice = if ($Answer -eq 'Yes') { 'steam' } else { 'epic' }
        } elseif ($SteamInstalled) { $Choice = 'steam' }
        elseif ($EpicApp) { $Choice = 'epic' }
        else { $Choice = 'custom' }
        Set-Content -LiteralPath $GameChoiceFile -Encoding ASCII -Value $Choice
    }
    if ($Choice -eq 'steam') {
        Start-Process 'steam://rungameid/518790'
    } elseif ($Choice -eq 'epic') {
        if ($EpicApp -notmatch '^[A-Za-z0-9_.-]+$') { throw 'ID de instalacao da Epic invalido.' }
        Start-Process ('com.epicgames.launcher://apps/' + $EpicApp + '?action=launch&silent=true')
    } else {
        $GameExe = ''
        if (Test-Path -LiteralPath $GamePathFile) { $GameExe = (Get-Content -LiteralPath $GamePathFile -Raw).Trim() }
        if (-not $GameExe -or -not (Test-Path -LiteralPath $GameExe)) {
            Add-Type -AssemblyName System.Windows.Forms
            $Picker = New-Object System.Windows.Forms.OpenFileDialog
            $Picker.Title = 'Localize o executavel theHunterCotW_F.exe'
            $Picker.Filter = 'theHunter (theHunterCotW_F.exe)|theHunterCotW_F.exe|Executaveis (*.exe)|*.exe'
            if ($Picker.ShowDialog() -ne 'OK') { throw 'Selecao do jogo cancelada.' }
            $GameExe = $Picker.FileName
            Set-Content -LiteralPath $GamePathFile -Encoding Unicode -Value $GameExe
        }
        Start-Process -FilePath $GameExe -WorkingDirectory (Split-Path -Parent $GameExe)
    }
    Write-LauncherLog ('Jogo iniciado: ' + $Choice)
} catch {
    Write-LauncherLog ('ERRO: ' + $_.Exception.Message)
    Add-Type -AssemblyName System.Windows.Forms
    [System.Windows.Forms.MessageBox]::Show($_.Exception.Message + "`n`nLog: " + $LogFile, 'Turbo Hunter + theHunter', 'OK', 'Error') | Out-Null
    exit 1
}
