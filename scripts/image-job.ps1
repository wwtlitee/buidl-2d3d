# GPU-serialized local text-to-image job. v0.2.0 / Unclecow.
param(
    [Parameter(Mandatory)][ValidatePattern('^[a-z0-9_-]+$')][string]$Engine,
    [Parameter(Mandatory)][string]$RequestJson,
    [Parameter(Mandatory)][ValidatePattern('^\d{8}-\d{6}-[a-f0-9]{6,32}$')][string]$RunId
)
$ErrorActionPreference='Stop'
$root=Split-Path $PSScriptRoot -Parent
$version=(Get-Content -Raw -LiteralPath (Join-Path $root 'VERSION.json')|ConvertFrom-Json).version
$folder=Join-Path $root "engines\$Engine"
$manifest=Get-Content -Raw -LiteralPath (Join-Path $folder 'engine.json')|ConvertFrom-Json
if($manifest.id -ne $Engine -or $manifest.kind -ne 'image' -or $manifest.status -notin @('ready','installed')){throw 'Image engine is not ready'}
$entry=[IO.Path]::GetFullPath((Join-Path $folder $manifest.entrypoint))
if(-not $entry.StartsWith($folder+[IO.Path]::DirectorySeparatorChar,[StringComparison]::OrdinalIgnoreCase)){throw 'Image entrypoint leaves its directory'}
$request=Get-Content -Raw -LiteralPath $RequestJson|ConvertFrom-Json
if(-not $request.prompt -or $request.prompt.Length -gt 3000){throw 'Image prompt is required (max 3000 chars)'}
$mutex=[Threading.Mutex]::new($false,'Local\Image2D3DFactoryGPU')
$locked=$false
try{
    try{$locked=$mutex.WaitOne(0)}catch [Threading.AbandonedMutexException]{$locked=$true}
    if(-not $locked){throw 'Another factory generation is using the GPU'}
    $output=Join-Path $root "outputs\$Engine\$RunId"
    if(Test-Path -LiteralPath $output){throw 'Output already exists; use a new run ID'}
    New-Item -ItemType Directory -Path $output|Out-Null
    $copied=Join-Path $output 'request.json'
    Copy-Item -LiteralPath $RequestJson -Destination $copied
    $receipt=[ordered]@{factoryVersion=$version;runId=$RunId;engine=$Engine;engineVersion=$manifest.version;inputMode='text';status='running';startedAt=[DateTimeOffset]::Now.ToString('o');request=$request}
    $receiptFile=Join-Path $output 'receipt.json'
    $receipt|ConvertTo-Json -Depth 6|Set-Content -LiteralPath $receiptFile -Encoding utf8NoBOM
    try{
        $global:LASTEXITCODE=0
        & (Join-Path $PSHOME 'pwsh.exe') -NoLogo -NoProfile -File $entry -RequestJson $copied -OutputDirectory $output 2>&1|Tee-Object -FilePath (Join-Path $output 'run.log')
        if($LASTEXITCODE -ne 0){throw "Image engine exit code: $LASTEXITCODE"}
        $images=@(Get-ChildItem -LiteralPath $output -File|Where-Object {$_.Extension.ToLower() -in @('.png','.jpg','.webp') -and $_.Length -gt 0})
        if(-not $images.Count){throw 'Image engine returned without a nonempty image'}
        $receipt.status='generated-unreviewed'
        $receipt.outputs=@($images|ForEach-Object{@{path=$_.FullName;bytes=$_.Length;sha256=(Get-FileHash -LiteralPath $_.FullName).Hash.ToLower()}})
    }catch{$receipt.status='failed';$receipt.error=$_.Exception.Message;throw}
    finally{$receipt.finishedAt=[DateTimeOffset]::Now.ToString('o');$receipt|ConvertTo-Json -Depth 6|Set-Content -LiteralPath $receiptFile -Encoding utf8NoBOM}
}finally{if($locked){$mutex.ReleaseMutex()};$mutex.Dispose()}
