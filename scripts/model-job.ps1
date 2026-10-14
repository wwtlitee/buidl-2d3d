# Shared GPU lock for rigging and animation. v0.4.0 / Unclecow.
param(
    [Parameter(Mandatory)][ValidatePattern('^[a-z0-9_-]+$')][string]$Engine,
    [Parameter(Mandatory)][string]$RequestJson,
    [Parameter(Mandatory)][ValidatePattern('^\d{8}-\d{6}-[a-f0-9]{6,32}$')][string]$RunId
)
$ErrorActionPreference='Stop'
$env:PYTHONUTF8='1'
[Console]::OutputEncoding=[Text.UTF8Encoding]::new($false)
$OutputEncoding=[Text.UTF8Encoding]::new($false)
$root=Split-Path $PSScriptRoot -Parent
$python=Join-Path $root 'runtime\core\.venv\Scripts\python.exe'
if(-not(Test-Path -LiteralPath $python -PathType Leaf)){$python=Join-Path $root 'engines\hunyuan3d\.venv\Scripts\python.exe'}
$mutex=[Threading.Mutex]::new($false,'Local\Image2D3DFactoryGPU')
$locked=$false
try{
    try{$locked=$mutex.WaitOne(0)}catch [Threading.AbandonedMutexException]{$locked=$true}
    if(-not $locked){throw 'Another factory generation is using the GPU; wait for the current job'}
    & $python -u (Join-Path $PSScriptRoot 'factory_model_job.py') --root $root --engine $Engine --request $RequestJson --run-id $RunId
    if($LASTEXITCODE -ne 0){throw "Model postprocessing exit code: $LASTEXITCODE"}
}finally{if($locked){$mutex.ReleaseMutex()};$mutex.Dispose()}
