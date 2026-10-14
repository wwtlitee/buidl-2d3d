# v0.1.0 / Unclecow: fail before invoking a GPU for invalid requests.
$ErrorActionPreference='Stop'
$factoryRoot=Split-Path $PSScriptRoot -Parent
$entry=Join-Path $factoryRoot 'scripts\factory.ps1'
$scripts=@(Get-ChildItem -LiteralPath (Join-Path $factoryRoot 'scripts') -Filter '*.ps1')+@(Get-ChildItem -LiteralPath (Join-Path $factoryRoot 'engines') -Filter 'run.ps1' -Recurse -Depth 1)
foreach($script in $scripts){
    $tokens=$null;$parseErrors=$null
    [Management.Automation.Language.Parser]::ParseFile($script.FullName,[ref]$tokens,[ref]$parseErrors)|Out-Null
    if($parseErrors.Count){throw "$script : $($parseErrors.Message -join '; ')"}
}
$list=& $entry -Action status -Json|ConvertFrom-Json
if(@($list|Where-Object id -EQ 'trellis').Count -ne 1){throw 'TRELLIS must be registered once'}
$required=@('trellis','pixal3d','hunyuan3d','hunyuan3d-mv','triposg','triposr','partcrafter','sf3d')
if(@($required|Where-Object {$_ -notin $list.id}).Count -or @($list|Group-Object id|Where-Object Count -gt 1).Count){throw 'Expected registered 3D engines with unique IDs'}
$before=@(Get-ChildItem -LiteralPath (Join-Path $factoryRoot 'outputs') -Recurse -Directory).Count
$rejected=$false
try{& $entry -Action run -Engine 'not-a-real-engine' -InputImage (Join-Path $factoryRoot 'inputs\kaiju-reference.png')}catch{if($_.Exception.Message -match 'Unknown or duplicate engine'){$rejected=$true}else{throw}}
if(-not $rejected){throw 'Unknown engine was not rejected'}
$rejected=$false
try{& $entry -Action run -Engine 'trellis' -InputImage (Join-Path $factoryRoot 'VERSION.json')}catch{if($_.Exception.Message -match 'Input must be'){$rejected=$true}else{throw}}
if(-not $rejected){throw 'Non-image input was not rejected'}
$after=@(Get-ChildItem -LiteralPath (Join-Path $factoryRoot 'outputs') -Recurse -Directory).Count
if($before -ne $after){throw 'Invalid requests created output directories'}
'PASS: script syntax, engine registry, invalid-engine rejection, non-image rejection, no invalid job side effects'
