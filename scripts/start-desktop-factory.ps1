# Desktop app owns its internal runtime. v0.5.0a / Unclecow.
param(
    [ValidateRange(1024,65535)][int]$Port=7860,
    [string]$StartupStatusPath
)
$ErrorActionPreference='Stop'
$root=Split-Path $PSScriptRoot -Parent
$desktopPython=Join-Path $root 'runtime\desktop\.venv\Scripts\pythonw.exe'
if(-not(Test-Path -LiteralPath $desktopPython -PathType Leaf)){throw '桌面运行环境缺失，请重新打开「造点啥.exe」自动准备。'}
$desktopLogs=Join-Path $root 'logs\desktop'
New-Item -ItemType Directory -Path $desktopLogs -Force|Out-Null
$stamp=(Get-Date -Format 'yyyyMMdd-HHmmss')+'-'+[guid]::NewGuid().ToString('N').Substring(0,6)
$statusPath=if($StartupStatusPath){[IO.Path]::GetFullPath($StartupStatusPath)}else{Join-Path $desktopLogs "$stamp-startup.json"}
if($StartupStatusPath){
    $logBoundary=[IO.Path]::GetFullPath((Join-Path $root 'logs')).TrimEnd([IO.Path]::DirectorySeparatorChar)+[IO.Path]::DirectorySeparatorChar
    if(-not $statusPath.StartsWith($logBoundary,[StringComparison]::OrdinalIgnoreCase)){throw '桌面启动回执必须位于当前应用的 logs 文件夹。'}
    New-Item -ItemType Directory -Path (Split-Path $statusPath -Parent) -Force|Out-Null
}
$desktopArgs=@('-u',('"'+(Join-Path $PSScriptRoot 'factory_desktop.py')+'"'),'--port',$Port,'--startup-status',('"'+$statusPath+'"'))
# A private pythonw launcher can itself be a console shim. Avoid creating its console
# without SW_HIDE, which would also hide the application's real GUI window.
$desktopProcess=Start-Process -FilePath $desktopPython -ArgumentList $desktopArgs -WorkingDirectory $root -NoNewWindow -PassThru -RedirectStandardOutput (Join-Path $desktopLogs "$stamp.log") -RedirectStandardError (Join-Path $desktopLogs "$stamp-error.log")
$deadline=[DateTime]::UtcNow.AddSeconds(55)
while([DateTime]::UtcNow -lt $deadline){
    if(Test-Path -LiteralPath $statusPath -PathType Leaf){
        $startup=Get-Content -Raw -LiteralPath $statusPath|ConvertFrom-Json
        if($startup.status -eq 'failed'){throw $startup.error}
        if($startup.status -in @('ready','foregrounded')){
            if($startup.visible -ne $true){throw '桌面尚未确认可见，不能报告已打开。'}
            if($startup.minimized -eq $true){throw '桌面尚未恢复可见，不能报告已打开。'}
            Write-Host "build something · 造点啥 v$($startup.version) 已打开；关闭应用时其内部任务一起关闭。"
            return [pscustomobject]@{status=$startup.status;pid=$startup.pid;startupStatusPath=$statusPath;visible=$true}
        }
    }
    if($desktopProcess.HasExited){throw "桌面启动失败，请查看 logs\desktop\$stamp-error.log"}
    Start-Sleep -Milliseconds 200
}
throw "桌面启动超时，请查看 logs\desktop\$stamp-error.log 后重新打开应用。"
