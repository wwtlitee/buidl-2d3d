# v0.1.0 / Unclecow. Isolated fixture; no model or GPU invocation.
$ErrorActionPreference='Stop'
$root=Split-Path $PSScriptRoot -Parent
$fixture=Join-Path $root ('outputs\_checks\lifecycle-'+[guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path "$fixture\scripts","$fixture\engines\fixture","$fixture\outputs" -Force|Out-Null
Copy-Item -LiteralPath "$root\scripts\factory.ps1" -Destination "$fixture\scripts\factory.ps1"
Copy-Item -LiteralPath "$root\VERSION.json" -Destination "$fixture\VERSION.json"
Copy-Item -LiteralPath "$root\inputs\kaiju-reference.png" -Destination "$fixture\input.png"
$manifest=@{id='fixture';title='Test fixture';status='ready';version='0.0.1';entrypoint='run.ps1';notes='No GPU'}
$manifest|ConvertTo-Json|Set-Content -LiteralPath "$fixture\engines\fixture\engine.json" -Encoding utf8NoBOM
$engineScript=@'
param([string]$InputImage,[string]$OutputDirectory)
$env:FACTORY_ISOLATION_CHECK='child'
$mode=Get-Content -LiteralPath (Join-Path $PSScriptRoot 'mode.txt') -Raw
if($mode.Trim() -eq 'failure'){exit 23}
if($mode.Trim() -eq 'missing'){exit 0}
@"
ply
format ascii 1.0
element vertex 3
property float x
property float y
property float z
element face 1
property list uchar int vertex_indices
end_header
0 0 0
1 0 0
0 1 0
3 0 1 2
"@|Set-Content -LiteralPath (Join-Path $OutputDirectory 'triangle.ply') -Encoding ascii
'child wrote a triangle'
exit 0
'@
$engineScript|Set-Content -LiteralPath "$fixture\engines\fixture\run.ps1" -Encoding utf8NoBOM
$saved=[Environment]::GetEnvironmentVariable('FACTORY_ISOLATION_CHECK','Process')
try{
    $env:FACTORY_ISOLATION_CHECK='parent'
    foreach($mode in @('success','failure','missing')){
        $mode|Set-Content -LiteralPath "$fixture\engines\fixture\mode.txt" -Encoding utf8NoBOM
        $thrown=$false
        try{& "$fixture\scripts\factory.ps1" -Action run -Engine fixture -InputImage "$fixture\input.png"|Out-Null}catch{$thrown=$true}
        if($thrown -ne ($mode -ne 'success')){throw "Incorrect exit propagation: $mode"}
        if($env:FACTORY_ISOLATION_CHECK -ne 'parent'){throw 'Engine mutated parent environment'}
        $latest=Get-ChildItem -LiteralPath "$fixture\outputs\fixture" -Directory|Sort-Object CreationTimeUtc -Descending|Select-Object -First 1
        $receipt=Get-Content -LiteralPath (Join-Path $latest.FullName 'receipt.json') -Raw|ConvertFrom-Json
        $expected=if($mode -eq 'success'){'generated-unreviewed'}else{'failed'}
        if($receipt.status -ne $expected -or -not $receipt.finishedAt){throw "Incorrect receipt: $mode"}
        if($mode -eq 'failure' -and $receipt.error -notmatch '23'){throw 'Missing engine failure code'}
        if($mode -eq 'missing' -and $receipt.error -notmatch 'without a nonempty'){throw 'Missing output failure not captured'}
    }
    $mutex=[Threading.Mutex]::new($false,'Local\Image2D3DFactoryGPU')
    if(-not $mutex.WaitOne(0)){throw 'Cannot acquire test mutex'}
    try{
        $hostExe=Join-Path $PSHOME 'pwsh.exe'
        $before=@(Get-ChildItem -LiteralPath "$fixture\outputs\fixture" -Directory).Count
        & $hostExe -NoProfile -File "$fixture\scripts\factory.ps1" -Action run -Engine fixture -InputImage "$fixture\input.png" *> "$fixture\mutex.log"
        if($LASTEXITCODE -eq 0){throw 'Concurrent factory job was not rejected'}
        if(@(Get-ChildItem -LiteralPath "$fixture\outputs\fixture" -Directory).Count -ne $before){throw 'Rejected concurrent job created output'}
    }finally{$mutex.ReleaseMutex();$mutex.Dispose()}
}finally{[Environment]::SetEnvironmentVariable('FACTORY_ISOLATION_CHECK',$saved,'Process')}
'PASS: process environment isolation, success receipt, nonzero failure, missing output failure, GPU mutual exclusion'
$global:LASTEXITCODE=0
