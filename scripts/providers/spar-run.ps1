# SPAR3D native model job entrypoint. v0.7.0 / Unclecow.
param([Parameter(Mandatory)][string]$InputImage,[Parameter(Mandatory)][string]$OutputDirectory,[string]$InputSet)
$ErrorActionPreference='Stop'
$factoryRoot=Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$python=Join-Path $factoryRoot 'engines/spar3d/.venv/Scripts/python.exe'
if(!(Test-Path -LiteralPath $python -PathType Leaf)){throw '请先在模型与环境部署 SPAR3D 的独立环境'}
$arguments=@('-u',(Join-Path $factoryRoot 'scripts/providers/spar-run.py'),'--root',$factoryRoot,'--input',$InputImage,'--output',$OutputDirectory)
if($InputSet){$arguments+=@('--input-set',$InputSet)}
& $python @arguments
if($LASTEXITCODE -ne 0){throw "SPAR3D执行失败: $LASTEXITCODE"}
