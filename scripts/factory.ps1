# 2D转3D工厂 v0.4.0 / Unclecow. Requires PowerShell 7.
param(
    [ValidateSet('menu','status','run')][string]$Action='menu',
    [string]$Engine,
    [string]$InputImage,
    [string]$InputSet,
    [ValidatePattern('^\d{8}-\d{6}-[a-f0-9]{6,32}$')][string]$RunId,
    [switch]$Json
)
$ErrorActionPreference='Stop'
$factoryRoot=Split-Path $PSScriptRoot -Parent
$factoryVersion=(Get-Content -Raw -LiteralPath (Join-Path $factoryRoot 'VERSION.json')|ConvertFrom-Json).version

function Get-FactoryEngines {
    foreach($folder in Get-ChildItem -LiteralPath (Join-Path $factoryRoot 'engines') -Directory -ErrorAction SilentlyContinue) {
        $manifest=Join-Path $folder.FullName 'engine.json'
        if(-not(Test-Path -LiteralPath $manifest)){continue}
        $item=Get-Content -Raw -LiteralPath $manifest|ConvertFrom-Json
        if($item.id -notmatch '^[a-z0-9_-]+$'){throw "Invalid engine id in $manifest"}
        $entry=if($item.entrypoint){[string]$item.entrypoint}else{'run.ps1'}
        $resolved=[IO.Path]::GetFullPath((Join-Path $folder.FullName $entry))
        if(-not $resolved.StartsWith($folder.FullName+[IO.Path]::DirectorySeparatorChar,[StringComparison]::OrdinalIgnoreCase)){throw "Engine entrypoint leaves its directory: $($item.id)"}
        [pscustomobject]@{id=$item.id;title=$item.title;status=$item.status;version=$item.version;entrypoint=$resolved;notes=$item.notes;capabilities=$item.capabilities;kind=$item.kind}
    }
}

function Invoke-FactoryJob([string]$EngineId,[string]$ImagePath) {
    $selected=@(Get-FactoryEngines|Where-Object id -EQ $EngineId)
    if($selected.Count -ne 1){throw "Unknown or duplicate engine: $EngineId"}
    $selected=$selected[0]
    if($selected.kind -in @('video','rig','animation')){throw 'This release focuses on local images and image-to-3D. New video, rigging and animation tasks are disabled; historical outputs remain available.'}
    if($selected.kind -eq 'image'){throw 'This is an image generator; use the local visual factory image page'}
    if($selected.kind -in @('texture','rig','animation')){throw 'Texturing, rigging and animation require a GLB; use factory-cli.py texture/rig/animate or the corresponding page'}
    if($selected.status -notin @('installed','ready')){throw "Engine is not ready: $EngineId ($($selected.status)). $($selected.notes)"}
    if(-not(Test-Path -LiteralPath $selected.entrypoint -PathType Leaf)){throw 'Engine entrypoint missing'}
    $image=(Get-Item -LiteralPath $ImagePath -ErrorAction Stop)
    if($image.PSIsContainer -or $image.Extension.ToLower() -notin @('.png','.jpg','.jpeg','.webp')){throw 'Input must be a PNG, JPEG or WebP file'}
    $sources=[ordered]@{front=$image.FullName}
    if($InputSet){
        if(-not $selected.capabilities.multiView){throw 'This engine does not support multi-view input'}
        $views=Get-Content -Raw -LiteralPath $InputSet|ConvertFrom-Json -AsHashtable
        if($views -isnot [System.Collections.IDictionary] -or -not $views.ContainsKey('front')){throw 'InputSet requires a front image'}
        foreach($view in $views.Keys){
            if($view -notin @('front','left','back','right')){throw "Unsupported view: $view"}
            $viewImage=Get-Item -LiteralPath ([string]$views[$view]) -ErrorAction Stop
            if($viewImage.PSIsContainer -or $viewImage.Extension.ToLower() -notin @('.png','.jpg','.jpeg','.webp')){throw 'InputSet must contain image files'}
            if($view -eq 'front' -and $viewImage.FullName -ne $image.FullName){throw 'InputSet front must match InputImage'}
            $sources[$view]=$viewImage.FullName
        }
    }
    $mutex=[Threading.Mutex]::new($false,'Local\Image2D3DFactoryGPU')
    $locked=$false
    try {
        try{$locked=$mutex.WaitOne(0)}catch [Threading.AbandonedMutexException]{$locked=$true}
        if(-not $locked){throw 'Another factory generation is using the GPU; wait for it to finish'}
        $jobRunId=if($RunId){$RunId}else{(Get-Date -Format 'yyyyMMdd-HHmmss')+'-'+[guid]::NewGuid().ToString('N').Substring(0,6)}
        $runRoot=Join-Path $factoryRoot "outputs\$EngineId\$jobRunId"
        if(Test-Path -LiteralPath $runRoot){throw 'Output already exists; use a new run ID'}
        New-Item -ItemType Directory -Path $runRoot|Out-Null
        $inputCopy=Join-Path $runRoot ('input'+$image.Extension.ToLower())
        Copy-Item -LiteralPath $image.FullName -Destination $inputCopy
        $copiedViews=[ordered]@{front=$inputCopy}
        foreach($view in $sources.Keys){
            if($view -eq 'front'){continue}
            $extension=[IO.Path]::GetExtension($sources[$view]).ToLower()
            $copy=Join-Path $runRoot "input_$view$extension"
            Copy-Item -LiteralPath $sources[$view] -Destination $copy
            $copiedViews[$view]=$copy
        }
        $copiedSet=Join-Path $runRoot 'input-set.json'
        $copiedViews|ConvertTo-Json|Set-Content -LiteralPath $copiedSet -Encoding utf8NoBOM
        $receipt=[ordered]@{factoryVersion=$factoryVersion;runId=$jobRunId;engine=$EngineId;engineVersion=$selected.version;status='running';source=$image.FullName;inputSha256=(Get-FileHash -LiteralPath $inputCopy).Hash.ToLower();inputMode=$(if($selected.capabilities.multiView){'multi-view'}else{'single'});inputs=@($copiedViews.Keys|ForEach-Object{@{view=$_;source=$sources[$_];path=$copiedViews[$_];sha256=(Get-FileHash -LiteralPath $copiedViews[$_]).Hash.ToLower()}});startedAt=[DateTimeOffset]::Now.ToString('o')}
        $receiptFile=Join-Path $runRoot 'receipt.json'
        $receipt|ConvertTo-Json -Depth 5|Set-Content -LiteralPath $receiptFile -Encoding utf8NoBOM
        Write-Host "Output: $runRoot"
        try {
            $global:LASTEXITCODE=0
            # Each engine may change process-level Python/cache variables.
            # A child host keeps later jobs in this menu independent.
            $engineHost=Join-Path $PSHOME 'pwsh.exe'
            $engineArgs=@('-NoLogo','-NoProfile','-File',$selected.entrypoint,'-InputImage',$inputCopy,'-OutputDirectory',$runRoot)
            if($selected.capabilities.multiView){$engineArgs+=@('-InputSet',$copiedSet)}
            & $engineHost @engineArgs 2>&1|Tee-Object -FilePath (Join-Path $runRoot 'run.log')
            if($LASTEXITCODE -ne 0){throw "Engine exit code: $LASTEXITCODE"}
            $assets=@(Get-ChildItem -LiteralPath $runRoot -Recurse -File|Where-Object {$_.Extension.ToLower() -in @('.glb','.obj','.ply') -and $_.Length -gt 0})
            if(-not $assets.Count){throw 'Engine returned without a nonempty model file'}
            $receipt.status='generated-unreviewed'
            $receipt.outputs=@($assets|ForEach-Object{[pscustomobject]@{path=$_.FullName;bytes=$_.Length;sha256=(Get-FileHash -LiteralPath $_.FullName).Hash.ToLower()}})
        }catch{$receipt.status='failed';$receipt.error=$_.Exception.Message;throw}
        finally{$receipt.finishedAt=[DateTimeOffset]::Now.ToString('o');$receipt|ConvertTo-Json -Depth 6|Set-Content -LiteralPath $receiptFile -Encoding utf8NoBOM}
    }finally{if($locked){$mutex.ReleaseMutex()};$mutex.Dispose()}
}

if($Action -eq 'status'){
    $items=@(Get-FactoryEngines)
    if($Json){ConvertTo-Json -InputObject $items -Depth 5}else{$items|Select-Object id,title,status,notes|Format-Table -Wrap}
    return
}
if($Action -eq 'run'){Invoke-FactoryJob $Engine $InputImage;return}
while($true){
    Write-Host "`n=== build something / 造点啥 v$factoryVersion ===" -ForegroundColor Cyan
    Get-FactoryEngines|Select-Object id,title,status|Format-Table|Out-Host
    Write-Host '输入工具 ID 开始生成；输入 q 退出。结果保存于 outputs，每次独立目录。'
    $choice=Read-Host '工具 ID'
    if($choice -eq 'q'){break}
    $path=(Read-Host '图片完整路径（可将文件拖入窗口）').Trim('"')
    try{Invoke-FactoryJob $choice $path;Write-Host '生成结束。请检查模型外观、贴图和拓扑。' -ForegroundColor Green}catch{Write-Host $_.Exception.Message -ForegroundColor Red}
}
