# Mock-only visible desktop launcher contract. v0.5.0a / Unclecow.
# Start-Process is replaced: no window, console, backend, or production PID is touched.
$ErrorActionPreference='Stop'
$root=Split-Path $PSScriptRoot -Parent
$global:factoryDesktopLauncherMock=@{captured=@();createdStatuses=@();status='ready';visible=$true}
function Start-Process {
    param([string]$FilePath,[object[]]$ArgumentList,[string]$WorkingDirectory,
          [string]$WindowStyle,[switch]$NoNewWindow,[switch]$PassThru,[string]$RedirectStandardOutput,[string]$RedirectStandardError)
    $global:factoryDesktopLauncherMock.captured+=@{filePath=$FilePath;windowStyle=$WindowStyle;noNewWindow=[bool]$NoNewWindow;arguments=$ArgumentList;workingDirectory=$WorkingDirectory;stdout=$RedirectStandardOutput;stderr=$RedirectStandardError}
    $statusPath=([string]$ArgumentList[-1]).Trim('"')
    $value=@{status=$global:factoryDesktopLauncherMock.status;visible=$global:factoryDesktopLauncherMock.visible;version='0.5.0a';pid=12345;error='mock startup failure'}
    [IO.File]::WriteAllText($statusPath,($value|ConvertTo-Json),[Text.UTF8Encoding]::new($false))
    $global:factoryDesktopLauncherMock.createdStatuses+=$statusPath
    return [pscustomobject]@{HasExited=$false}
}
$checks=@()
try {
    & (Join-Path $root 'scripts/start-desktop-factory.ps1')
    $captured=$global:factoryDesktopLauncherMock.captured[-1]
    if($captured.filePath -ne (Join-Path $root 'runtime/desktop/.venv/Scripts/pythonw.exe')){throw 'Launcher did not select the independent pythonw runtime'}
    if(-not $captured.noNewWindow -or $captured.windowStyle){throw 'Desktop startup must suppress console allocation without hiding the application GUI'}
    if(-not $captured.stdout -or -not $captured.stderr -or $captured.stdout -eq $captured.stderr){throw 'Desktop startup must retain independent stdout and stderr logs'}
    $checks+=@{name='console-shim-no-new-window-visible-ready';passed=$true}
    $requestedStatus=Join-Path $root ('logs/desktop/fixture-requested-'+[guid]::NewGuid().ToString('N')+'.json')
    & (Join-Path $root 'scripts/start-desktop-factory.ps1') -StartupStatusPath $requestedStatus
    if(([string]$global:factoryDesktopLauncherMock.captured[-1].arguments[-1]).Trim('"') -ne $requestedStatus){throw 'Launcher did not forward the caller-owned status receipt'}
    $checks+=@{name='caller-owned-startup-receipt-forwarded';passed=$true}
    foreach($status in @('ready','foregrounded')){
        $global:factoryDesktopLauncherMock.status=$status
        $global:factoryDesktopLauncherMock.visible=$false
        $rejected=$false
        try { & (Join-Path $root 'scripts/start-desktop-factory.ps1') }
        catch {if($_.Exception.Message -like '*尚未确认可见*'){$rejected=$true}else{throw}}
        if(-not $rejected){throw "Launcher accepted invisible $status"}
        $checks+=@{name="invisible-$status-rejected";passed=$true}
    }
    $global:factoryDesktopLauncherMock.status='failed'
    $rejected=$false
    try { & (Join-Path $root 'scripts/start-desktop-factory.ps1') }
    catch {if($_.Exception.Message -eq 'mock startup failure'){$rejected=$true}else{throw}}
    if(-not $rejected){throw 'Launcher accepted failed startup'}
    $checks+=@{name='failed-startup-rejected';passed=$true}
    $report=@{version='0.5.0a';status='passed';mode='mock-only';nativeWindowsOperated=$false;backendStarted=$false;checks=$checks}
    $reportPath=Join-Path $root 'logs/desktop-launcher-mock-acceptance.json'
    [IO.File]::WriteAllText($reportPath,($report|ConvertTo-Json -Depth 5),[Text.UTF8Encoding]::new($false))
    $report|ConvertTo-Json -Depth 5
}
finally {
    foreach($path in $global:factoryDesktopLauncherMock.createdStatuses){
        $resolved=[IO.Path]::GetFullPath($path)
        $expected=[IO.Path]::GetFullPath((Join-Path $root 'logs/desktop'))+[IO.Path]::DirectorySeparatorChar
        if(-not $resolved.StartsWith($expected,[StringComparison]::OrdinalIgnoreCase)){throw 'Mock status cleanup target outside own log directory'}
        Remove-Item -LiteralPath $resolved -ErrorAction SilentlyContinue
    }
}
