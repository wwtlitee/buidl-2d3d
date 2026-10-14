param([Parameter(Mandatory)][string]$InputImage,[Parameter(Mandatory)][string]$OutputDirectory,[ValidateSet(512,1024)][int]$Resolution=512)
$ErrorActionPreference='Stop'
New-Item -ItemType Directory -Path $OutputDirectory -Force|Out-Null
$binary=Join-Path $PSScriptRoot 'runtime\trellis-cli.exe'
& $binary $InputImage (Join-Path $OutputDirectory 'model.glb') --models (Join-Path $PSScriptRoot 'models-q8') --res $Resolution --atlas 2048 --seed 20261003 --steps 12 --webp off --require-gpu --threads 8 --verbose
if($LASTEXITCODE -ne 0){throw "TRELLIS failed: $LASTEXITCODE"}
