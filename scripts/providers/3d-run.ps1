# Shape extensions use the original image/model job contract. v0.7.0 / Unclecow.
param([Parameter(Mandatory)][string]$InputImage,[Parameter(Mandatory)][string]$OutputDirectory,[string]$InputSet)
$ErrorActionPreference='Stop'
$factoryRoot=Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$engineId=Split-Path $PSScriptRoot -Leaf
$catalog=Get-Content -LiteralPath (Join-Path $factoryRoot 'config/image3d-models.json') -Raw -Encoding UTF8 | ConvertFrom-Json
$recipe=@($catalog.models|Where-Object {$_.engineId -eq $engineId})[0]
if(!$recipe){throw '建模配方不存在'}
$python=Join-Path $factoryRoot $recipe.runtime.python
if(!(Test-Path -LiteralPath $python -PathType Leaf)){throw '建模独立环境尚未部署，请在模型与环境安装'}
$arguments=@('-u',(Join-Path $factoryRoot 'scripts/providers/3d-run.py'),'--root',$factoryRoot,'--engine',$engineId,'--input',$InputImage,'--output',$OutputDirectory)
if($InputSet){$arguments+=@('--input-set',$InputSet)}
& $python @arguments
if($LASTEXITCODE -ne 0){throw "建模执行失败: $LASTEXITCODE"}
