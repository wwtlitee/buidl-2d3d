# v0.4.0 / Unclecow. Starts one hidden loopback service, leaves existing jobs alone.
param([int]$Port=7860,[switch]$NoBrowser)
$ErrorActionPreference='Stop'
$root=Split-Path $PSScriptRoot -Parent
function Get-ServiceFingerprint {
    param([string]$ScriptDirectory=$PSScriptRoot)
    $buffer=[IO.MemoryStream]::new()
    $sha=[Security.Cryptography.SHA256]::Create()
    $utf8=[Text.UTF8Encoding]::new($false)
    try{
        foreach($name in @('factory_queue.py','factory_web.py','factory_glb.py','factory_postprocess.py','factory_setup.py','factory_runtime.py','factory_pipeline.py','factory_app_runtime.py','factory_desktop.py','factory_deploy.py','factory_model_job.py','factory_image.py','model-job.ps1','image-job.ps1','factory.ps1','../config/setup-catalog.json','../config/python-models.json','../config/python-profiles.json')){
            if(-not (Test-Path -LiteralPath (Join-Path $ScriptDirectory $name) -PathType Leaf)){continue}
            $nameBytes=$utf8.GetBytes($name)
            $buffer.Write($nameBytes,0,$nameBytes.Length)
            $buffer.WriteByte(0)
            $sourceBytes=[IO.File]::ReadAllBytes((Join-Path $ScriptDirectory $name))
            $buffer.Write($sourceBytes,0,$sourceBytes.Length)
            $buffer.WriteByte(0)
        }
        return ([BitConverter]::ToString($sha.ComputeHash($buffer.ToArray()))).Replace('-','').ToLowerInvariant()
    }finally{$buffer.Dispose();$sha.Dispose()}
}
$version=(Get-Content -Raw -LiteralPath (Join-Path $root 'VERSION.json')|ConvertFrom-Json).version
$serviceBuild=Get-ServiceFingerprint -ScriptDirectory $PSScriptRoot
$url="http://127.0.0.1:$Port"
$active=$null
try{$active=Invoke-RestMethod "$url/api/health" -TimeoutSec 2}catch{}
if($active){
    if($active.service -ne '2D3DFactory'){throw "端口 $Port 已被其他服务占用，请使用 -Port 指定另一个端口"}
    if($active.version -ne $version){throw "旧版服务 $($active.version) 仍在运行，请先完成旧任务再停止旧服务。不会自动中断任务。"}
    if(-not $active.serviceBuild){throw '当前服务缺少构建指纹，请先完成当前任务，然后关闭旧服务并重新启动。不会自动中断任务。'}
    if($active.serviceBuild -ne $serviceBuild){throw '当前运行服务的代码与磁盘构建不同，请先完成当前任务，然后关闭旧服务并重新启动。不会自动中断任务。'}
}else{
    $python=Join-Path $root 'runtime\core\.venv\Scripts\python.exe'
    if(-not(Test-Path -LiteralPath $python)){$python=Join-Path $root 'engines\hunyuan3d\.venv\Scripts\python.exe'}
    if(-not(Test-Path -LiteralPath $python)){throw '运行环境缺失，请重新打开「造点啥.exe」自动准备；无需先安装模型。'}
    $logs=Join-Path $root 'logs\visual-factory'
    New-Item -ItemType Directory -Path $logs -Force|Out-Null
    $stamp=(Get-Date -Format 'yyyyMMdd-HHmmss')+'-'+[guid]::NewGuid().ToString('N').Substring(0,6)
    $process=Start-Process -FilePath $python -ArgumentList @('-u',('"'+(Join-Path $root 'scripts\factory_web.py')+'"'),'--port',$Port) -WorkingDirectory $root -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $logs "$stamp-server.log") -RedirectStandardError (Join-Path $logs "$stamp-server-error.log")
    $process.Id|Set-Content -LiteralPath (Join-Path $logs 'server.pid') -Encoding utf8NoBOM
    $ready=$false
    for($i=0;$i -lt 40;$i++){
        if($process.HasExited){throw "可视化服务启动失败，请查看 logs\visual-factory\$stamp-server-error.log"}
        try{$health=Invoke-RestMethod "$url/api/health" -TimeoutSec 1;if($health.service -eq '2D3DFactory' -and $health.version -eq $version -and $health.serviceBuild -eq $serviceBuild){$ready=$true;break}}catch{}
        Start-Sleep -Milliseconds 250
    }
    if(-not $ready){throw '服务尚未就绪，请查看 logs/visual-factory 中的服务日志'}
}
Write-Host "build something · 造点啥 v$version · $url"
if(-not $NoBrowser){Start-Process $url}
