# Turbo Hunter 0.4.1 - GUI installer
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
[System.Windows.Forms.Application]::EnableVisualStyles()

$Here = Split-Path -Parent $MyInvocation.MyCommand.Path
$InternalDir = Split-Path -Parent $Here
$BaseDir = Split-Path -Parent $InternalDir
$RuntimeDir = Join-Path $InternalDir 'runtime'
$AssetsDir = Join-Path $InternalDir 'assets'
$StatusFile = Join-Path $RuntimeDir 'install_status.json'
$WorkerFile = Join-Path $Here 'InstallerWorker.ps1'
$StartLauncher = Join-Path $BaseDir 'INICIAR TURBO HUNTER.vbs'
$InstallLog = Join-Path $RuntimeDir 'instalacao.log'
$IconPath = Join-Path $AssetsDir 'turbo_hunter.ico'
$DeerPng = Join-Path $AssetsDir 'turbo_hunter_deer.png'
New-Item -ItemType Directory -Force -Path $RuntimeDir | Out-Null

$cultureName = [Globalization.CultureInfo]::CurrentUICulture.Name
$IsPt = $cultureName.ToLowerInvariant().StartsWith('pt')
function T([string]$Pt, [string]$En) { if ($IsPt) { return $Pt } return $En }

$bg = [Drawing.Color]::FromArgb(15,19,22)
$card = [Drawing.Color]::FromArgb(25,31,35)
$text = [Drawing.Color]::FromArgb(242,245,246)
$muted = [Drawing.Color]::FromArgb(166,176,183)
$accent = [Drawing.Color]::FromArgb(239,145,43)
$success = [Drawing.Color]::FromArgb(69,184,119)
$danger = [Drawing.Color]::FromArgb(220,91,91)

$form = New-Object Windows.Forms.Form
$form.Text = T 'Turbo Hunter 0.4.1 - Instalação' 'Turbo Hunter 0.4.1 - Installation'
$form.ClientSize = New-Object Drawing.Size(650,390)
$form.StartPosition = 'CenterScreen'
$form.FormBorderStyle = 'FixedSingle'
$form.MaximizeBox = $false
$form.BackColor = $bg
$form.Font = New-Object Drawing.Font('Segoe UI',10)
if (Test-Path -LiteralPath $IconPath) {
    try { $form.Icon = New-Object Drawing.Icon($IconPath) } catch {}
}

$deer = New-Object Windows.Forms.PictureBox
$deer.BackColor = $bg
$deer.Location = New-Object Drawing.Point(30,19)
$deer.Size = New-Object Drawing.Size(58,64)
$deer.SizeMode = [Windows.Forms.PictureBoxSizeMode]::Zoom
if (Test-Path -LiteralPath $DeerPng) {
    try { $deer.Image = [Drawing.Image]::FromFile($DeerPng) } catch {}
}
$form.Controls.Add($deer)

$title = New-Object Windows.Forms.Label
$title.Text = 'TURBO HUNTER 0.4.1'
$title.Font = New-Object Drawing.Font('Segoe UI',22,[Drawing.FontStyle]::Bold)
$title.ForeColor = $text
$title.BackColor = $bg
$title.Location = New-Object Drawing.Point(96,24)
$title.Size = New-Object Drawing.Size(520,42)
$form.Controls.Add($title)

$subtitle = New-Object Windows.Forms.Label
$subtitle.Text = T 'Localizador de Abates - theHunter: Call of the Wild' 'Kill Locator - theHunter: Call of the Wild'
$subtitle.Font = New-Object Drawing.Font('Segoe UI',10)
$subtitle.ForeColor = $muted
$subtitle.BackColor = $bg
$subtitle.Location = New-Object Drawing.Point(100,66)
$subtitle.Size = New-Object Drawing.Size(500,24)
$form.Controls.Add($subtitle)

$panel = New-Object Windows.Forms.Panel
$panel.BackColor = $card
$panel.Location = New-Object Drawing.Point(28,112)
$panel.Size = New-Object Drawing.Size(594,150)
$form.Controls.Add($panel)

$small = New-Object Windows.Forms.Label
$small.Text = T 'PRONTO' 'READY'
$small.Font = New-Object Drawing.Font('Segoe UI',9,[Drawing.FontStyle]::Bold)
$small.ForeColor = $accent
$small.BackColor = $card
$small.Location = New-Object Drawing.Point(22,16)
$small.Size = New-Object Drawing.Size(180,22)
$panel.Controls.Add($small)

$statusTitle = New-Object Windows.Forms.Label
$statusTitle.Text = T 'Instalar Turbo Hunter' 'Install Turbo Hunter'
$statusTitle.Font = New-Object Drawing.Font('Segoe UI',17,[Drawing.FontStyle]::Bold)
$statusTitle.ForeColor = $text
$statusTitle.BackColor = $card
$statusTitle.Location = New-Object Drawing.Point(22,43)
$statusTitle.Size = New-Object Drawing.Size(545,36)
$panel.Controls.Add($statusTitle)

$statusDetail = New-Object Windows.Forms.Label
$statusDetail.Text = T 'Um clique prepara Python, Frida e o iniciador automaticamente.' 'One click prepares Python, Frida and the launcher automatically.'
$statusDetail.Font = New-Object Drawing.Font('Segoe UI',10)
$statusDetail.ForeColor = $muted
$statusDetail.BackColor = $card
$statusDetail.Location = New-Object Drawing.Point(22,88)
$statusDetail.Size = New-Object Drawing.Size(545,44)
$panel.Controls.Add($statusDetail)

$elapsed = New-Object Windows.Forms.Label
$elapsed.Text = ''
$elapsed.Font = New-Object Drawing.Font('Segoe UI',9,[Drawing.FontStyle]::Bold)
$elapsed.ForeColor = $accent
$elapsed.BackColor = $card
$elapsed.TextAlign = 'MiddleRight'
$elapsed.Location = New-Object Drawing.Point(420,16)
$elapsed.Size = New-Object Drawing.Size(145,22)
$panel.Controls.Add($elapsed)

$progress = New-Object Windows.Forms.ProgressBar
$progress.Location = New-Object Drawing.Point(28,282)
$progress.Size = New-Object Drawing.Size(594,16)
$progress.Style = 'Blocks'
$progress.Value = 0
$form.Controls.Add($progress)

$button = New-Object Windows.Forms.Button
$button.Text = T 'INSTALAR' 'INSTALL'
$button.Font = New-Object Drawing.Font('Segoe UI',12,[Drawing.FontStyle]::Bold)
$button.ForeColor = [Drawing.Color]::FromArgb(25,25,25)
$button.BackColor = $accent
$button.FlatStyle = 'Flat'
$button.FlatAppearance.BorderSize = 0
$button.Location = New-Object Drawing.Point(28,317)
$button.Size = New-Object Drawing.Size(594,52)
$form.Controls.Add($button)

$worker = $null
$started = $null
$lastState = ''

function Start-Install {
    if (-not (Test-Path -LiteralPath $WorkerFile)) {
        [Windows.Forms.MessageBox]::Show((T 'Os arquivos internos do instalador não foram encontrados.' 'Installer internal files were not found.'),'Turbo Hunter 0.4.1','OK','Error') | Out-Null
        return
    }
    Remove-Item -LiteralPath $StatusFile -Force -ErrorAction SilentlyContinue
    $script:started = Get-Date
    $script:lastState = 'working'
    $button.Enabled = $false
    $button.Text = T 'INSTALANDO...' 'INSTALLING...'
    $progress.Style = 'Marquee'
    $progress.MarqueeAnimationSpeed = 28
    $small.Text = T 'EM ANDAMENTO' 'IN PROGRESS'
    $small.ForeColor = $accent
    $statusTitle.ForeColor = $text
    $statusTitle.Text = T 'Preparando instalação' 'Preparing installation'
    $statusDetail.Text = T 'Aguarde. O Turbo Hunter fará as etapas automaticamente.' 'Please wait. Turbo Hunter will complete the steps automatically.'
    $args = '-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "' + $WorkerFile + '"'
    $script:worker = Start-Process -FilePath 'powershell.exe' -ArgumentList $args -PassThru -WindowStyle Hidden
}

function Open-TurboHunter {
    if (Test-Path -LiteralPath $StartLauncher) {
        Start-Process -FilePath 'wscript.exe' -ArgumentList ('"' + $StartLauncher + '"')
        $form.Close()
    } else {
        [Windows.Forms.MessageBox]::Show((T 'O iniciador ainda não foi criado. Execute a instalação novamente.' 'The launcher has not been created yet. Run the installation again.'),'Turbo Hunter 0.4.1','OK','Error') | Out-Null
    }
}

$button.Add_Click({
    if ($script:lastState -eq 'done') { Open-TurboHunter }
    else { Start-Install }
})

$timer = New-Object Windows.Forms.Timer
$timer.Interval = 500
$timer.Add_Tick({
    if ($script:started) {
        $span = (Get-Date) - $script:started
        $elapsed.Text = ('{0:00}:{1:00}' -f [int]$span.TotalMinutes, $span.Seconds)
    }
    if (Test-Path -LiteralPath $StatusFile) {
        try {
            $data = Get-Content -LiteralPath $StatusFile -Raw | ConvertFrom-Json
            $statusTitle.Text = [string]$data.title
            $statusDetail.Text = [string]$data.detail
            if ($data.step -gt 0) {
                $small.Text = if ($IsPt) { 'ETAPA ' + $data.step + ' DE 3' } else { 'STEP ' + $data.step + ' OF 3' }
            }
            if ($data.state -eq 'done' -and $script:lastState -ne 'done') {
                $script:lastState = 'done'
                $progress.Style = 'Blocks'; $progress.Value = 100
                $small.Text = T 'PRONTO' 'READY'; $small.ForeColor = $success
                $statusTitle.ForeColor = $success
                $button.Enabled = $true; $button.Text = T 'ABRIR TURBO HUNTER' 'OPEN TURBO HUNTER'; $button.BackColor = $success
                $elapsed.Text = T 'Concluído' 'Completed'
            } elseif ($data.state -eq 'error' -and $script:lastState -ne 'error') {
                $script:lastState = 'error'
                $progress.Style = 'Blocks'; $progress.Value = 0
                $small.Text = T 'ERRO' 'ERROR'; $small.ForeColor = $danger
                $statusTitle.ForeColor = $danger
                $button.Enabled = $true; $button.Text = T 'TENTAR NOVAMENTE' 'TRY AGAIN'; $button.BackColor = $accent
                $elapsed.Text = ''
                $logHint = T 'Detalhes: TurboHunter\runtime\instalacao.log' 'Details: TurboHunter\runtime\instalacao.log'
                [Windows.Forms.MessageBox]::Show(([string]$data.detail + "`r`n`r`n" + $logHint),'Turbo Hunter 0.4.1','OK','Error') | Out-Null
            }
        } catch {}
    } elseif ($script:worker -and $script:worker.HasExited -and $script:lastState -eq 'working') {
        $script:lastState = 'error'
        $progress.Style = 'Blocks'; $progress.Value = 0
        $small.Text = T 'ERRO' 'ERROR'; $small.ForeColor = $danger
        $statusTitle.Text = T 'O instalador foi encerrado inesperadamente' 'The installer ended unexpectedly'
        $statusTitle.ForeColor = $danger
        $statusDetail.Text = T 'Clique em TENTAR NOVAMENTE. Se repetir, envie o arquivo instalacao.log.' 'Click TRY AGAIN. If it happens again, send the instalacao.log file.'
        $button.Enabled = $true; $button.Text = T 'TENTAR NOVAMENTE' 'TRY AGAIN'; $button.BackColor = $accent
    }
})
$timer.Start()

$form.Add_FormClosed({
    try { if ($deer.Image) { $deer.Image.Dispose() } } catch {}
})

[void]$form.ShowDialog()
