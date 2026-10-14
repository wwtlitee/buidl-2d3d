# MV-Adapter six-view joint inference. v0.7.0 / Unclecow.
param([Parameter(Mandatory)][string]$RequestJson,[Parameter(Mandatory)][string]$OutputDirectory)
$ErrorActionPreference='Stop'
$factoryRoot=Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$python=Join-Path $factoryRoot 'engines/mvadapter-i2mv-sdxl/.venv/Scripts/python.exe'
if(!(Test-Path -LiteralPath $python -PathType Leaf)){throw '请先在模型与环境部署 MV-Adapter 联合多视角环境'}
& $python -u (Join-Path $factoryRoot 'scripts/providers/multiview-run.py') --root $factoryRoot --request $RequestJson --output $OutputDirectory
if($LASTEXITCODE -ne 0){throw "联合多视角生成失败: $LASTEXITCODE"}
