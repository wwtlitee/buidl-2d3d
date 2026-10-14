param([Parameter(Mandatory)][string]$InputImage,[Parameter(Mandatory)][string]$OutputDirectory,[ValidateSet(512,1024)][int]$Resolution=1024,[switch]$GeometryOnly)
$ErrorActionPreference='Stop'
$nativeRoot=Join-Path (Split-Path $PSScriptRoot -Parent) 'trellis'
New-Item -ItemType Directory -Path $OutputDirectory -Force|Out-Null
$arguments=@($InputImage,(Join-Path $OutputDirectory 'model.glb'),'--models',(Join-Path $nativeRoot 'models-q8'),'--model','pixal3d','--res',"$Resolution",'--atlas','2048','--seed','20261003','--steps','12','--webp','off','--require-gpu','--threads','8','--max-tokens','24576','--verbose')
if($GeometryOnly -or $Resolution -eq 512){$arguments+='--no-texture'}
$previous=[Environment]::GetEnvironmentVariable('TRELLIS_NAF_CONV_DIRECT','Process')
try{
    $env:TRELLIS_NAF_CONV_DIRECT='1'
    & (Join-Path $nativeRoot 'runtime\trellis-cli.exe') @arguments
    if($LASTEXITCODE -ne 0){throw "Pixal3D failed: $LASTEXITCODE"}
}finally{[Environment]::SetEnvironmentVariable('TRELLIS_NAF_CONV_DIRECT',$previous,'Process')}
