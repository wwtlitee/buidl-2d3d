param(
    [Parameter(Mandatory=$true)][string]$RequestJson,
    [Parameter(Mandatory=$true)][string]$OutputDirectory,
    [string]$EngineId
)
$ErrorActionPreference = 'Stop'
$factoryRoot = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
if (-not $EngineId) {
    $videoRequest = Get-Content -LiteralPath $RequestJson -Raw -Encoding UTF8 | ConvertFrom-Json
    $EngineId = $videoRequest.engineId
    if (-not $EngineId) { $EngineId = $videoRequest.engine }
}
if (-not $EngineId) { throw '视频请求缺少模型标识' }
$videoPython = Join-Path $factoryRoot 'video-lab/wangp/.venv/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $videoPython -PathType Leaf)) { throw '请在模型与环境中下载部署视频运行环境' }
$videoAdapter = Join-Path $factoryRoot 'scripts/providers/video_wangp.py'
& $videoPython $videoAdapter --root $factoryRoot --engine $EngineId --request $RequestJson --output $OutputDirectory
if ($LASTEXITCODE -ne 0) { throw "视频模型执行失败（退出码 $LASTEXITCODE）" }
