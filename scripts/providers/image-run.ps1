# Image extensions using a reviewed isolated environment. v0.7.0 / Unclecow.
param([Parameter(Mandatory)][string]$RequestJson,[Parameter(Mandatory)][string]$OutputDirectory,[switch]$ValidateOnly)
$ErrorActionPreference='Stop'
$factoryRoot=Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$engineId=Split-Path $PSScriptRoot -Leaf
$catalog=Get-Content -LiteralPath (Join-Path $factoryRoot 'config/image3d-models.json') -Raw -Encoding UTF8|ConvertFrom-Json
$recipe=@($catalog.models|Where-Object { $_.engineId -eq $engineId -or (!$_.engineId -and $_.id -eq $engineId) })[0]
if(!$recipe){throw '图片模型配方不存在'}
$pythonRelative=if($recipe.runtime.python){$recipe.runtime.python}else{'runtime/core/.venv/Scripts/python.exe'}
$python=Join-Path $factoryRoot $pythonRelative
if(!(Test-Path -LiteralPath $python -PathType Leaf)){throw '此图片模型的独立环境尚未安装完成，请打开模型与环境。'}
$arguments=@('-u',(Join-Path $factoryRoot 'scripts/providers/image-run.py'),'--root',$factoryRoot,'--engine',$engineId,'--request',$RequestJson,'--output',$OutputDirectory)
if($ValidateOnly){$arguments+='--validate-only'}
& $python @arguments
if($LASTEXITCODE -ne 0){throw "图片模型推理失败: $LASTEXITCODE"}
