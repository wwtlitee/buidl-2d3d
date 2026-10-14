# build something v0.6.0 / Unclecow.
# ASCII source for Windows PowerShell 5.1 UTF-8-without-BOM compatibility.
param(
    [ValidateSet('desktop','web','cli')][string]$Mode='desktop',
    [string]$Root=(Split-Path $PSScriptRoot -Parent),
    [ValidateRange(1024,65535)][int]$Port=7860,
    [switch]$CheckOnly,
    [switch]$PrepareOnly
)
$ErrorActionPreference='Stop'
$Root=[IO.Path]::GetFullPath($Root)
$corePython=Join-Path $Root 'runtime/core/.venv/Scripts/python.exe'
$desktopPython=Join-Path $Root 'runtime/desktop/.venv/Scripts/pythonw.exe'
$portableShell=Join-Path $Root 'runtime/tools/pwsh/pwsh.exe'
$globalShell=Get-Command pwsh -ErrorAction SilentlyContinue
function Test-RuntimePython([string]$Python,[string]$Modules){
    if(-not (Test-Path -LiteralPath $Python -PathType Leaf)){return $false}
    try{& $Python -c ('import '+$Modules) *> $null; return $LASTEXITCODE -eq 0}catch{return $false}
}
function Test-RuntimeCommand([string]$Executable){
    if(-not (Test-Path -LiteralPath $Executable -PathType Leaf)){return $false}
    try{& $Executable --version *> $null; return $LASTEXITCODE -eq 0}catch{return $false}
}
function Get-DesktopRuntimeVersion([string]$Python){
    if(-not (Test-Path -LiteralPath $Python -PathType Leaf)){return $null}
    try{
        $detected=@(& $Python -c 'import sys; sys.path.insert(0, sys.argv[1]); from factory_desktop import runtime_version; print(runtime_version())' (Join-Path $Root 'scripts') 2>$null)
        if($LASTEXITCODE -eq 0 -and $detected.Count -eq 1 -and [string]$detected[0] -match '^\d+\.\d+\.\d+\.\d+$'){
            return [string]$detected[0]
        }
    }catch{}
    return $null
}
function Test-ElevatedProcess {
    $identity=[Security.Principal.WindowsIdentity]::GetCurrent()
    try{return ([Security.Principal.WindowsPrincipal]::new($identity)).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)}
    finally{$identity.Dispose()}
}
function Ensure-DesktopRuntime([string]$Python,$Item){
    $version=Get-DesktopRuntimeVersion $Python
    if($version){Write-Host ('Desktop component ready: WebView2 '+$version);return}
    if(Test-ElevatedProcess){throw 'WebView2 is missing. Reopen the app normally, without administrator privileges, to install this desktop component for your user.'}
    if(-not $Item -or $Item.url -ne 'https://go.microsoft.com/fwlink/p/?LinkId=2124703'){
        throw 'The official Microsoft WebView2 bootstrapper URL is missing or invalid. Restore config/bootstrap-runtime.json and reopen the app.'
    }
    $downloads=Join-Path $Root 'cache/bootstrap'
    New-Item -ItemType Directory -Path $downloads -Force|Out-Null
    $installer=Join-Path $downloads ('MicrosoftEdgeWebview2Setup-'+[guid]::NewGuid().ToString('N')+'.exe')
    Write-Host 'Preparing the desktop component: downloading Microsoft WebView2...'
    try{Invoke-WebRequest -UseBasicParsing -Uri $Item.url -OutFile $installer -TimeoutSec 180}
    catch{throw ('WebView2 download failed. Reopen the app to retry, or install the official runtime from https://developer.microsoft.com/microsoft-edge/webview2/. '+$_.Exception.Message)}
    $signature=Get-AuthenticodeSignature -LiteralPath $installer
    if($signature.Status -ne 'Valid' -or -not $signature.SignerCertificate){
        throw ('WebView2 installer signature is invalid. The file was not executed: '+$installer+'. Reopen the app to download again.')
    }
    $publisher=$signature.SignerCertificate.GetNameInfo([Security.Cryptography.X509Certificates.X509NameType]::SimpleName,$false)
    if($publisher -cne 'Microsoft Corporation'){
        throw ('WebView2 installer is not signed by Microsoft Corporation. The file was not executed: '+$installer)
    }
    Write-Host 'Installing the verified Microsoft desktop component for your user...'
    $process=Start-Process -FilePath $installer -ArgumentList @('/silent','/install') -WorkingDirectory $Root -WindowStyle Hidden -Wait -PassThru
    if($process.ExitCode -ne 0){throw ('WebView2 installation failed with exit code '+$process.ExitCode+'. Reopen the app to retry; the application has not been launched.')}
    $version=Get-DesktopRuntimeVersion $Python
    if(-not $version){throw 'WebView2 was not detected after installation. Reopen the app, or repair the official runtime from https://developer.microsoft.com/microsoft-edge/webview2/.'}
    Write-Host ('Desktop component ready: WebView2 '+$version)
}
function Ensure-PrivateVenv([string]$Uv,[string]$PythonVersion,[string]$EnvironmentDirectory,[string]$Interpreter,[switch]$RequireWindowed){
    $windowedReady=(-not $RequireWindowed) -or (Test-Path -LiteralPath (Join-Path $EnvironmentDirectory 'Scripts/pythonw.exe') -PathType Leaf)
    if((Test-RuntimePython $Interpreter 'sys') -and $windowedReady){return}
    if(Test-Path -LiteralPath $EnvironmentDirectory){
        $boundary=[IO.Path]::GetFullPath($Root).TrimEnd([IO.Path]::DirectorySeparatorChar)+[IO.Path]::DirectorySeparatorChar
        $resolvedEnvironment=[IO.Path]::GetFullPath($EnvironmentDirectory)
        $backup=$resolvedEnvironment+'.incomplete-'+[guid]::NewGuid().ToString('N').Substring(0,8)
        if(-not $resolvedEnvironment.StartsWith($boundary,[StringComparison]::OrdinalIgnoreCase) -or -not $backup.StartsWith($boundary,[StringComparison]::OrdinalIgnoreCase)){
            throw 'Private runtime recovery path is outside the application directory.'
        }
        Move-Item -LiteralPath $resolvedEnvironment -Destination $backup
        Write-Host ('Incomplete runtime was preserved: '+$backup)
    }
    & $Uv venv --python $PythonVersion $EnvironmentDirectory
    if($LASTEXITCODE -ne 0){throw 'Could not create private runtime. Reopen startup to retry.'}
}
if($CheckOnly){
    $shellReady=if(Test-Path -LiteralPath $portableShell -PathType Leaf){Test-RuntimeCommand $portableShell}else{[bool]$globalShell}
    $desktopDependenciesReady=(Test-Path -LiteralPath $desktopPython -PathType Leaf) -and (Test-RuntimePython (Join-Path $Root 'runtime/desktop/.venv/Scripts/python.exe') 'webview,PIL')
    $webview2Version=Get-DesktopRuntimeVersion (Join-Path $Root 'runtime/desktop/.venv/Scripts/python.exe')
    $webview2Ready=[bool]$webview2Version
    @{version='0.6.0';coreReady=(Test-RuntimePython $corePython 'PIL,numpy,trimesh');desktopReady=($desktopDependenciesReady -and $webview2Ready);shellReady=$shellReady;webview2Ready=$webview2Ready;webview2Version=$webview2Version}|ConvertTo-Json -Compress
    return
}
if(-not [Environment]::Is64BitOperatingSystem){throw 'build something requires 64-bit Windows 10/11.'}
$architecture=[Environment]::GetEnvironmentVariable('PROCESSOR_ARCHITECTURE')
if($architecture -eq 'ARM64'){throw 'This release supports Windows x64. ARM64 model runtimes are not supported.'}
[Net.ServicePointManager]::SecurityProtocol=[Net.SecurityProtocolType]::Tls12
$spec=Get-Content -Raw -Encoding UTF8 -LiteralPath (Join-Path $Root 'config/bootstrap-runtime.json')|ConvertFrom-Json
Add-Type -AssemblyName System.IO.Compression.FileSystem

function Install-VerifiedArchive($Item,[string]$Name,[string]$Destination){
    $downloads=Join-Path $Root 'cache/bootstrap'
    New-Item -ItemType Directory -Path $downloads -Force|Out-Null
    $archive=Join-Path $downloads ($Name+'-'+$Item.version+'.zip')
    $part=$archive+'.part'
    if(Test-Path -LiteralPath $archive -PathType Leaf){
        if((Get-Item -LiteralPath $archive).Length -ne $Item.size -or (Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash.ToLowerInvariant() -ne $Item.sha256){
            $quarantine=$archive+'.invalid-'+[guid]::NewGuid().ToString('N').Substring(0,8)
            Move-Item -LiteralPath $archive -Destination $quarantine
            Write-Host ('Invalid cached archive was preserved: '+$quarantine)
        }
    }
    if(-not (Test-Path -LiteralPath $archive -PathType Leaf) -and (Test-Path -LiteralPath $part -PathType Leaf) -and (Get-Item -LiteralPath $part).Length -ge $Item.size){
        if((Get-Item -LiteralPath $part).Length -eq $Item.size -and (Get-FileHash -LiteralPath $part -Algorithm SHA256).Hash.ToLowerInvariant() -eq $Item.sha256){
            Move-Item -LiteralPath $part -Destination $archive
        }else{
            $quarantine=$part+'.invalid-'+[guid]::NewGuid().ToString('N').Substring(0,8)
            Move-Item -LiteralPath $part -Destination $quarantine
            Write-Host ('Invalid partial archive was preserved: '+$quarantine)
        }
    }
    if(-not (Test-Path -LiteralPath $archive -PathType Leaf)){
        Write-Host ('Downloading '+$Name+' '+$Item.version+' from official release...')
        $curl=Get-Command curl.exe -ErrorAction SilentlyContinue
        if($curl){
            & $curl.Source --fail --location --retry 3 --connect-timeout 30 --max-time 1800 --continue-at - --output $part $Item.url
            if($LASTEXITCODE -ne 0){throw ('Download failed. Reopen startup to resume. Partial file: '+$part)}
        }else{Invoke-WebRequest -UseBasicParsing -Uri $Item.url -OutFile $part -TimeoutSec 1800}
        if((Get-Item -LiteralPath $part).Length -ne $Item.size -or (Get-FileHash -LiteralPath $part -Algorithm SHA256).Hash.ToLowerInvariant() -ne $Item.sha256){
            $quarantine=$part+'.invalid-'+[guid]::NewGuid().ToString('N').Substring(0,8)
            Move-Item -LiteralPath $part -Destination $quarantine
            throw ('Download integrity check failed. Evidence saved: '+$quarantine+'. Reopen startup to download again.')
        }
        Move-Item -LiteralPath $part -Destination $archive
    }
    if((Get-Item -LiteralPath $archive).Length -ne $Item.size -or (Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash.ToLowerInvariant() -ne $Item.sha256){throw ('Cached archive integrity check failed: '+$archive)}
    $zip=[IO.Compression.ZipFile]::OpenRead($archive)
    try{
        $boundary=[IO.Path]::GetFullPath($Destination)+[IO.Path]::DirectorySeparatorChar
        foreach($entry in $zip.Entries){
            $target=[IO.Path]::GetFullPath((Join-Path $Destination $entry.FullName))
            if(-not $target.StartsWith($boundary,[StringComparison]::OrdinalIgnoreCase)){throw 'Archive contains a path outside its runtime directory.'}
        }
    }finally{$zip.Dispose()}
    New-Item -ItemType Directory -Path $Destination -Force|Out-Null
    Expand-Archive -LiteralPath $archive -DestinationPath $Destination -Force
}

$saved=@{}
foreach($name in @('UV_PYTHON_INSTALL_DIR','UV_PYTHON_BIN_DIR','UV_CACHE_DIR','UV_PYTHON_PREFERENCE','UV_NO_MODIFY_PATH')){$saved[$name]=[Environment]::GetEnvironmentVariable($name,'Process')}
$bootstrapMutex=[Threading.Mutex]::new($false,('Local\BuildSomethingBootstrap-'+([BitConverter]::ToString([Security.Cryptography.SHA256]::Create().ComputeHash([Text.Encoding]::UTF8.GetBytes($Root)))).Replace('-','').Substring(0,16)))
$locked=$false
try{
    try{$locked=$bootstrapMutex.WaitOne(0)}catch [Threading.AbandonedMutexException]{$locked=$true}
    if(-not $locked){throw 'Startup preparation is already running. Wait for the first startup window.'}
    $shell=$portableShell
    if(-not (Test-RuntimeCommand $shell)){
        if(-not (Test-Path -LiteralPath $shell -PathType Leaf) -and $globalShell){$shell=$globalShell.Source}else{
            Install-VerifiedArchive $spec.powershell 'powershell' (Join-Path $Root 'runtime/tools/pwsh')
        }
    }
    $needsCore=-not (Test-RuntimePython $corePython 'PIL,numpy,trimesh')
    $needsDesktop=$Mode -eq 'desktop' -and -not ((Test-Path -LiteralPath $desktopPython -PathType Leaf) -and (Test-RuntimePython (Join-Path $Root 'runtime/desktop/.venv/Scripts/python.exe') 'webview,PIL'))
    if($needsCore -or $needsDesktop){
        $uv=Join-Path $Root 'runtime/tools/uv/uv.exe'
        if(-not (Test-RuntimeCommand $uv)){Install-VerifiedArchive $spec.uv 'uv' (Join-Path $Root 'runtime/tools/uv')}
        $env:UV_PYTHON_INSTALL_DIR=Join-Path $Root 'runtime/python'
        $env:UV_PYTHON_BIN_DIR=Join-Path $Root 'runtime/tools/python-bin'
        $env:UV_CACHE_DIR=Join-Path $Root 'cache/uv'
        $env:UV_PYTHON_PREFERENCE='only-managed'
        $env:UV_NO_MODIFY_PATH='1'
        Write-Host 'Preparing private Python. Your system Python and PATH are not changed.'
        & $uv python install $spec.python --no-bin
        if($LASTEXITCODE -ne 0){throw 'Python download failed. Reopen startup to retry; check network/proxy access to GitHub.'}
        if($needsCore){
            Ensure-PrivateVenv $uv $spec.python (Join-Path $Root 'runtime/core/.venv') $corePython
            & $uv pip sync --python $corePython --require-hashes (Join-Path $Root 'config/core-requirements.lock')
            if($LASTEXITCODE -ne 0){throw 'Core dependencies installation failed. Reopen startup to retry.'}
        }
        if($needsDesktop){
            Ensure-PrivateVenv $uv $spec.python (Join-Path $Root 'runtime/desktop/.venv') (Join-Path $Root 'runtime/desktop/.venv/Scripts/python.exe') -RequireWindowed
            & $uv pip sync --python (Join-Path $Root 'runtime/desktop/.venv/Scripts/python.exe') --require-hashes (Join-Path $Root 'config/desktop-requirements.lock')
            if($LASTEXITCODE -ne 0){throw 'Desktop dependencies installation failed. Reopen the app to retry.'}
        }
    }
    if($Mode -eq 'desktop'){
        Ensure-DesktopRuntime (Join-Path $Root 'runtime/desktop/.venv/Scripts/python.exe') $spec.webview2
    }
    if($PrepareOnly){Write-Host 'Private runtime is prepared. AI model installation is available inside the app.';return}
    if($Mode -eq 'desktop'){
        & $shell -NoProfile -File (Join-Path $Root 'scripts/start-desktop-factory.ps1') -Port $Port
    }elseif($Mode -eq 'web'){& $shell -NoProfile -File (Join-Path $Root 'scripts/start-visual-factory.ps1') -Port $Port}
    else{& $shell -NoProfile -File (Join-Path $Root 'scripts/factory.ps1') -Action menu}
    if($LASTEXITCODE -ne 0){throw ('Startup failed with exit code '+$LASTEXITCODE+'. See logs for details.')}
}finally{
    foreach($name in $saved.Keys){[Environment]::SetEnvironmentVariable($name,$saved[$name],'Process')}
    if($locked){$bootstrapMutex.ReleaseMutex()}
    $bootstrapMutex.Dispose()
}
