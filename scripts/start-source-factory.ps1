# Source checkout entry, v0.9.1b / Unclecow. ASCII for Windows PowerShell 5.1.
param([switch]$PrepareOnly)
$ErrorActionPreference = 'Stop'
$root = [IO.Path]::GetFullPath((Split-Path $PSScriptRoot -Parent))
$utf8 = New-Object Text.UTF8Encoding($false)
[Console]::OutputEncoding = $utf8
$appName = ([char]0x9020).ToString() + ([char]0x70B9).ToString() + ([char]0x5565).ToString() + '.exe'
$exePath = Join-Path $root $appName
$versionPath = Join-Path $root 'VERSION.json'
$sourcePath = Join-Path $PSScriptRoot 'desktop_launcher.cs'
$builderPath = Join-Path $PSScriptRoot 'build-desktop-launcher.ps1'
$logDirectory = Join-Path $root 'logs/desktop-launcher/source'
$stamp = (Get-Date -Format 'yyyyMMdd-HHmmss-fff') + '-' + $PID + '-' + [guid]::NewGuid().ToString('N').Substring(0, 6)
$logPath = $null
$resultPath = $null
$mutex = $null
$lockHeld = $false
$exitCode = 1
$result = [ordered]@{ ok = $false; prepareOnly = [bool]$PrepareOnly; root = $root; exePath = $exePath }

function Write-SourceLog([string]$Message) {
    if ($script:logPath) {
        [IO.File]::AppendAllText($script:logPath, ('[{0}] {1}{2}' -f (Get-Date).ToString('o'), $Message, [Environment]::NewLine), $script:utf8)
    }
}

function Get-PreparedLauncher {
    if (-not (Test-Path -LiteralPath $exePath -PathType Leaf)) { return @{ valid = $false; reason = 'Native launcher is missing.' } }
    if (-not (Test-Path -LiteralPath ($exePath + '.build.json') -PathType Leaf)) { return @{ valid = $false; reason = 'Native build receipt is missing.' } }
    try {
        $receipt = Get-Content -Raw -Encoding UTF8 -LiteralPath ($exePath + '.build.json') | ConvertFrom-Json
        $actualHash = (Get-FileHash -LiteralPath $exePath -Algorithm SHA256).Hash.ToLowerInvariant()
        $metadata = (Get-Item -LiteralPath $exePath).VersionInfo
        if ($receipt.version -ne $productVersion -or $receipt.sourceSha256 -ne $sourceHash -or $receipt.versionSha256 -ne $versionHash -or $receipt.sha256 -ne $actualHash -or $receipt.target -ne 'winexe' -or $metadata.ProductVersion -ne $productVersion -or $metadata.FileVersion -ne $fileVersion) {
            return @{ valid = $false; reason = 'Native launcher, source, version, or build receipt has changed.' }
        }
        return @{ valid = $true; reason = 'Verified native launcher reused.'; sha256 = $actualHash; productVersion = $metadata.ProductVersion }
    } catch {
        return @{ valid = $false; reason = ('Native launcher validation failed: ' + $_.Exception.Message) }
    }
}

try {
    # A checkout can be opened from PowerShell 7, whose inherited module paths
    # may otherwise select incompatible modules in Windows PowerShell 5.1.
    foreach ($module in @('Microsoft.PowerShell.Utility', 'Microsoft.PowerShell.Management')) {
        Import-Module (Join-Path $PSHOME ('Modules/' + $module + '/' + $module + '.psd1')) -Force
    }
    try { [IO.Directory]::CreateDirectory($logDirectory) | Out-Null }
    catch {
        $logDirectory = Join-Path ([IO.Path]::GetTempPath()) 'BuildSomething-source-launcher'
        [IO.Directory]::CreateDirectory($logDirectory) | Out-Null
    }
    $logPath = Join-Path $logDirectory ($stamp + '.log')
    $resultPath = Join-Path $logDirectory ($stamp + '.json')
    $result.logPath = $logPath
    Write-SourceLog ('Source preparation started in ' + $root)
    $hasher = [Security.Cryptography.SHA256]::Create()
    try { $rootHash = ([BitConverter]::ToString($hasher.ComputeHash($utf8.GetBytes($root.TrimEnd('\').ToLowerInvariant())))).Replace('-', '') }
    finally { $hasher.Dispose() }
    $mutex = New-Object Threading.Mutex($false, ('Local\BuildSomething.SourceLauncher.' + $rootHash))
    Write-SourceLog 'Waiting for the source preparation lock.'
    try { $lockHeld = $mutex.WaitOne(120000) }
    catch [Threading.AbandonedMutexException] { $lockHeld = $true }
    if (-not $lockHeld) { throw 'Another launcher is still preparing this checkout. Try again after it finishes.' }
    foreach ($required in @($versionPath, $sourcePath, $builderPath)) {
        if (-not (Test-Path -LiteralPath $required -PathType Leaf)) { throw ('Required source file is missing: ' + $required) }
    }
    $productVersion = [string](Get-Content -Raw -Encoding UTF8 -LiteralPath $versionPath | ConvertFrom-Json).version
    if ($productVersion -notmatch '^(?<major>\d+)\.(?<minor>\d+)\.(?<patch>\d+)(?<revision>[a-z]*)$') { throw 'VERSION.json contains an invalid product version.' }
    foreach ($segment in @('major', 'minor', 'patch')) {
        if ([long]$Matches[$segment] -gt 65535) { throw 'A numeric version segment exceeds the Windows version-field limit.' }
    }
    $fileVersion = $Matches['major'] + '.' + $Matches['minor'] + '.' + $Matches['patch'] + '.0'
    $sourceHash = (Get-FileHash -LiteralPath $sourcePath -Algorithm SHA256).Hash.ToLowerInvariant()
    $versionHash = (Get-FileHash -LiteralPath $versionPath -Algorithm SHA256).Hash.ToLowerInvariant()
    $prepared = Get-PreparedLauncher
    $result.reason = $prepared.reason
    Write-SourceLog $prepared.reason
    $result.action = 'reused'
    if (-not $prepared.valid) {
        $result.action = 'built'
        & $builderPath -OutputPath $exePath *>&1 | ForEach-Object { Write-SourceLog ([string]$_) }
        $prepared = Get-PreparedLauncher
        if (-not $prepared.valid) { throw ('Built launcher failed verification: ' + $prepared.reason) }
    }
    $result.version = $productVersion
    $result.exeProductVersion = $prepared.productVersion
    $result.sha256 = $prepared.sha256
    $result.sourceSha256 = $sourceHash
    $result.versionSha256 = $versionHash
    if (-not $PrepareOnly) {
        # The requested application is visible; this preparation host stays hidden.
        $application = Start-Process -FilePath $exePath -WorkingDirectory $root -WindowStyle Normal -PassThru
        $result.launchedPid = $application.Id
        Write-SourceLog ('Native application started: PID ' + $application.Id)
    }
    $result.ok = $true
    $exitCode = 0
    Write-SourceLog ('Source preparation finished: ' + $result.action)
} catch {
    $result.error = $_.Exception.Message
    try { Write-SourceLog ($_.Exception.ToString()) } catch { }
    if (-not $PrepareOnly) {
        try {
            Add-Type -AssemblyName System.Windows.Forms
            $message = 'Could not start Build Something.' + [Environment]::NewLine + [Environment]::NewLine + $result.error
            if ($logPath) { $message += [Environment]::NewLine + [Environment]::NewLine + 'Details: ' + $logPath }
            [Windows.Forms.MessageBox]::Show($message, 'Build Something - Startup failed', [Windows.Forms.MessageBoxButtons]::OK, [Windows.Forms.MessageBoxIcon]::Error) | Out-Null
        } catch { }
    }
} finally {
    if ($lockHeld) { $mutex.ReleaseMutex() }
    if ($mutex) { $mutex.Dispose() }
    $json = $result | ConvertTo-Json -Depth 4
    if ($resultPath) {
        try { [IO.File]::WriteAllText($resultPath, $json, $utf8) }
        catch { [Console]::Error.WriteLine('Could not save startup receipt: ' + $_.Exception.Message) }
    }
    $writer = New-Object IO.StreamWriter([Console]::OpenStandardOutput(), $utf8)
    try { $writer.WriteLine($json); $writer.Flush() } finally { $writer.Dispose() }
}
exit $exitCode
