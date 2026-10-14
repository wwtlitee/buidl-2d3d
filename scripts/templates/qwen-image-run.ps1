# Native image adapter using the application's private runtime. v0.5.0 / Unclecow.
param([Parameter(Mandatory)][string]$RequestJson,[Parameter(Mandatory)][string]$OutputDirectory,[switch]$ValidateOnly)
$ErrorActionPreference='Stop'
$factoryRoot=Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$adapterPython=Join-Path $factoryRoot 'runtime\core\.venv\Scripts\python.exe'
if(-not(Test-Path -LiteralPath $adapterPython -PathType Leaf)){throw '基础运行环境缺失，请重新打开造点啥.exe自动准备。'}
$arguments=@('-u',(Join-Path $PSScriptRoot 'run.py'),'--request',$RequestJson,'--output',$OutputDirectory)
if($ValidateOnly){$arguments+='--validate-only'}
& $adapterPython @arguments
if($LASTEXITCODE -ne 0){throw "Qwen Image 2.1 failed: $LASTEXITCODE"}
