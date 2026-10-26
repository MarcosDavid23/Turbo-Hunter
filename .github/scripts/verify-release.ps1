$ErrorActionPreference = 'Stop'
$root = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\..'))
$manifest = Get-Content -LiteralPath (Join-Path $root '.github\release.json') -Raw | ConvertFrom-Json
$archive = Join-Path $root $manifest.asset
if ((Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash -ine $manifest.sha256) {
    throw 'Installer checksum does not match the approved package.'
}
Add-Type -AssemblyName System.IO.Compression.FileSystem
$zip = [IO.Compression.ZipFile]::OpenRead($archive)
$sha = [Security.Cryptography.SHA256]::Create()
$files = 0
try {
    foreach ($entry in $zip.Entries) {
        if ($entry.FullName.EndsWith('/')) { continue }
        if (-not $entry.FullName.StartsWith('Turbo Hunter/', [StringComparison]::Ordinal)) {
            throw 'The installer must extract into a Turbo Hunter folder.'
        }
        $relative = $entry.FullName.Substring('Turbo Hunter/'.Length)
        $source = [IO.Path]::GetFullPath((Join-Path $root $relative))
        if (-not $source.StartsWith(($root.TrimEnd('\') + '\'), [StringComparison]::OrdinalIgnoreCase)) {
            throw 'Invalid archive path.'
        }
        if (-not (Test-Path -LiteralPath $source -PathType Leaf)) { throw ('Missing source file: ' + $relative) }
        $stream = $entry.Open()
        try { $archiveHash = [BitConverter]::ToString($sha.ComputeHash($stream)).Replace('-', '') }
        finally { $stream.Dispose() }
        if ((Get-FileHash -LiteralPath $source -Algorithm SHA256).Hash -ine $archiveHash) {
            throw ('Source differs from the installer: ' + $relative)
        }
        $files++
    }
} finally { $sha.Dispose(); $zip.Dispose() }
if ($files -ne 19) { throw 'Unexpected installer file count.' }
foreach ($script in @(Get-ChildItem -LiteralPath (Join-Path $root 'TurboHunter\installer') -Filter '*.ps1' -File)) {
    $tokens = $null; $parseErrors = $null
    [void][Management.Automation.Language.Parser]::ParseFile($script.FullName, [ref]$tokens, [ref]$parseErrors)
    if ($parseErrors.Count -gt 0) { throw ($script.Name + ': ' + ($parseErrors.Message -join '; ')) }
}
$config = Get-Content -LiteralPath (Join-Path $root 'TurboHunter\app\hud_config.json') -Raw | ConvertFrom-Json
if ($config.lab_config_version -ne $manifest.version) { throw 'Default settings version mismatch.' }
Write-Host ('Approved ZIP verified; ' + $files + ' source files match; Windows PowerShell syntax checked.')
