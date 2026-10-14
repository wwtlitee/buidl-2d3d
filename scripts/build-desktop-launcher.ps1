# Windowed EXE build, v0.6.0 / Unclecow. ASCII source works in PowerShell 5.1.
param([string]$OutputPath)
$ErrorActionPreference='Stop'
$root=Split-Path $PSScriptRoot -Parent
$versionPath=Join-Path $root 'VERSION.json'
$productVersion=[string](Get-Content -Raw -Encoding UTF8 -LiteralPath $versionPath|ConvertFrom-Json).version
if($productVersion -notmatch '^(?<major>\d+)\.(?<minor>\d+)\.(?<patch>\d+)(?<revision>[a-z]*)$'){
    throw 'VERSION.json must contain MAJOR.MINOR.PATCH plus an optional lowercase revision suffix.'
}
foreach($segment in @('major','minor','patch')){
    if([long]$Matches[$segment] -gt 65535){throw 'A numeric version segment exceeds the Windows version-field limit.'}
}
$fileVersion=$Matches['major']+'.'+$Matches['minor']+'.'+$Matches['patch']+'.0'
if(-not $OutputPath){
    $appName=([char]0x9020).ToString()+([char]0x70B9).ToString()+([char]0x5565).ToString()+'.exe'
    $OutputPath=Join-Path $root $appName
}
$OutputPath=[IO.Path]::GetFullPath($OutputPath)
$directory=Split-Path $OutputPath -Parent
New-Item -ItemType Directory -Path $directory -Force|Out-Null
$compiler=Join-Path $env:WINDIR 'Microsoft.NET/Framework64/v4.0.30319/csc.exe'
if(-not (Test-Path -LiteralPath $compiler -PathType Leaf)){
    $compiler=Join-Path $env:WINDIR 'Microsoft.NET/Framework/v4.0.30319/csc.exe'
}
if(-not (Test-Path -LiteralPath $compiler -PathType Leaf)){throw 'Windows .NET Framework 4 compiler was not found.'}
$source=Join-Path $PSScriptRoot 'desktop_launcher.cs'
$temporary=Join-Path $directory ([IO.Path]::GetFileNameWithoutExtension($OutputPath)+'.build-'+[guid]::NewGuid().ToString('N')+'.exe')
$compiledSource=$temporary+'.cs'
try{
    $template=[IO.File]::ReadAllText($source,[Text.Encoding]::UTF8)
    if(-not $template.Contains('__PRODUCT_VERSION__') -or -not $template.Contains('__FILE_VERSION__')){
        throw 'Native launcher source is missing the product-version or file-version build token.'
    }
    $resolvedSource=$template.Replace('__PRODUCT_VERSION__',$productVersion).Replace('__FILE_VERSION__',$fileVersion)
    [IO.File]::WriteAllText($compiledSource,$resolvedSource,[Text.UTF8Encoding]::new($false))
    & $compiler /nologo /target:winexe /platform:anycpu /optimize+ /warnaserror+ /utf8output "/out:$temporary" /reference:System.dll /reference:System.Core.dll /reference:System.Drawing.dll /reference:System.Windows.Forms.dll /reference:System.Web.Extensions.dll $compiledSource
    if($LASTEXITCODE -ne 0){throw ('Native launcher build failed with exit code '+$LASTEXITCODE)}
    $metadata=(Get-Item -LiteralPath $temporary).VersionInfo
    if($metadata.ProductVersion -ne $productVersion -or $metadata.FileVersion -ne $fileVersion){
        throw 'Native launcher full product version or Windows file-version mapping is inconsistent.'
    }
    $stamp=(Get-Date -Format 'yyyyMMdd-HHmmss-fff')+'-'+[guid]::NewGuid().ToString('N').Substring(0,6)
    if(Test-Path -LiteralPath $OutputPath -PathType Leaf){
        $oldHash=(Get-FileHash -LiteralPath $OutputPath -Algorithm SHA256).Hash
        $newHash=(Get-FileHash -LiteralPath $temporary -Algorithm SHA256).Hash
        if($oldHash -ne $newHash){
            $history=Join-Path $directory 'logs/desktop-launcher/builds'
            New-Item -ItemType Directory -Path $history -Force|Out-Null
            $preserved=Join-Path $history ([IO.Path]::GetFileNameWithoutExtension($OutputPath)+'.previous-build-'+$stamp+'.exe')
            Copy-Item -LiteralPath $OutputPath -Destination $preserved
            if(Test-Path -LiteralPath ($OutputPath+'.build.json') -PathType Leaf){
                Copy-Item -LiteralPath ($OutputPath+'.build.json') -Destination ($preserved+'.build.json')
            }
        }
    }
    Move-Item -LiteralPath $temporary -Destination $OutputPath -Force
    $receipt=@{
        product='build something';version=$productVersion;fileVersion=$fileVersion;
        versionMapping='Full product version is retained in ProductVersion; Windows numeric FileVersion maps to MAJOR.MINOR.PATCH.0.';
        owner='Unclecow';build=$stamp;target='winexe';output=$OutputPath;
        sha256=(Get-FileHash -LiteralPath $OutputPath -Algorithm SHA256).Hash.ToLowerInvariant();
        sourceSha256=(Get-FileHash -LiteralPath $source -Algorithm SHA256).Hash.ToLowerInvariant();
        compiledSourceSha256=(Get-FileHash -LiteralPath $compiledSource -Algorithm SHA256).Hash.ToLowerInvariant();
        versionSha256=(Get-FileHash -LiteralPath $versionPath -Algorithm SHA256).Hash.ToLowerInvariant();
        compiler=$compiler;builtAt=(Get-Date).ToString('o')
    }
    $receiptPath=$OutputPath+'.build.json'
    [IO.File]::WriteAllText($receiptPath,($receipt|ConvertTo-Json -Depth 4),[Text.UTF8Encoding]::new($false))
    $receipt|ConvertTo-Json -Depth 4
}finally{
    if(Test-Path -LiteralPath $temporary -PathType Leaf){Remove-Item -LiteralPath $temporary}
    if(Test-Path -LiteralPath $compiledSource -PathType Leaf){Remove-Item -LiteralPath $compiledSource}
}
