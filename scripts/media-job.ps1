# Shared owned GPU invocation for image editing and video. v0.7.0 / Unclecow.
param(
 [Parameter(Mandatory)][ValidatePattern('^[a-z0-9_-]+$')][string]$Engine,
 [Parameter(Mandatory)][string]$RequestJson,
 [Parameter(Mandatory)][ValidatePattern('^\d{8}-\d{6}-[a-f0-9]{6,32}$')][string]$RunId
)
$ErrorActionPreference='Stop'
$root=Split-Path $PSScriptRoot -Parent
$mutex=[Threading.Mutex]::new($false,'Local\Image2D3DFactoryGPU')
$locked=$false
try{
 try{$locked=$mutex.WaitOne(0)}catch [Threading.AbandonedMutexException]{$locked=$true}
 if(-not $locked){throw 'Another factory generation is using the GPU'}
 $python=Join-Path $root 'runtime/core/.venv/Scripts/python.exe'
 if(-not(Test-Path -LiteralPath $python)){$python=Join-Path $root 'runtime/desktop/.venv/Scripts/python.exe'}
 if(-not(Test-Path -LiteralPath $python)){$python=(Get-Command python -ErrorAction Stop).Source}
 # uv/venv launchers may inject another interpreter's stdlib into descendants.
 # Every selected interpreter must resolve its own installation and environment.
 Remove-Item Env:PYTHONHOME -ErrorAction SilentlyContinue
 Remove-Item Env:PYTHONPATH -ErrorAction SilentlyContinue
 & $python (Join-Path $root 'scripts/factory_media_job.py') --root $root --engine $Engine --request $RequestJson --run-id $RunId
 if($LASTEXITCODE -ne 0){exit $LASTEXITCODE}
}finally{if($locked){$mutex.ReleaseMutex()};$mutex.Dispose()}
