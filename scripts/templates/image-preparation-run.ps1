# Internal background removal support. v0.5.0 / Unclecow.
param([Parameter(Mandatory)][string]$InputImage,[Parameter(Mandatory)][string]$OutputDirectory)
$ErrorActionPreference='Stop'
$nativeRoot=Join-Path (Split-Path $PSScriptRoot -Parent) 'trellis'
New-Item -ItemType Directory -Path $OutputDirectory -Force|Out-Null
& (Join-Path $nativeRoot 'runtime\trellis-cli.exe') $InputImage (Join-Path $OutputDirectory 'prepared.png') --models (Join-Path $nativeRoot 'models-q8') --bg-only --require-gpu --threads 8
if($LASTEXITCODE -ne 0){throw "Image preparation failed: $LASTEXITCODE"}
