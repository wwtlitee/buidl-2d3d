# 2D转3D工厂 v0.0.1 / Unclecow. PowerShell 7.
$ErrorActionPreference = 'Stop'
$factoryRoot = Split-Path $PSScriptRoot -Parent
$modelRoot = Join-Path $factoryRoot 'engines\trellis\models-q8'
$installRoot = Join-Path $factoryRoot 'engines\pixal3d'
New-Item -ItemType Directory -Path $installRoot -Force | Out-Null
$requests = @(
    @{repo='ilintar/trellis2-gguf'; revision='a57397bd3d351599d9729fc144b3f87c3f87d65b'; folder='q8'; names=@('shape_flow_1024.gguf','tex_flow_1024.gguf','birefnet.gguf')},
    @{repo='vegax87/Pixal3D'; revision='51de7d1764f4b735c4e3aaea40e5ba60c18ca707'; folder=''; names=@('pixal3d_ss_flow.gguf','pixal3d_shape_flow_512.gguf','pixal3d_shape_flow_1024.gguf','pixal3d_tex_flow_1024.gguf','pixal3d_naf.gguf')}
)
$jobs = @()
foreach ($request in $requests) {
    $listingUrl = "https://huggingface.co/api/models/$($request.repo)/tree/$($request.revision)"
    if ($request.folder) { $listingUrl += '/' + $request.folder }
    $listing = Invoke-RestMethod ($listingUrl + '?expand=false')
    foreach ($name in $request.names) {
        $relative = if ($request.folder) { $request.folder + '/' + $name } else { $name }
        $item = @($listing | Where-Object path -EQ $relative)
        if ($item.Count -ne 1 -or -not $item[0].lfs.oid) { throw "Missing upstream checksum: $relative" }
        $jobs += [pscustomobject]@{name=$name; size=$item[0].size; sha256=$item[0].lfs.oid; destination=(Join-Path $modelRoot $name); url="https://huggingface.co/$($request.repo)/resolve/$($request.revision)/$relative"}
    }
}
$jobs | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $installRoot 'download-manifest.json') -Encoding utf8NoBOM
$results = $jobs | ForEach-Object -Parallel {
    $job = $_
    try {
        if (-not (Test-Path -LiteralPath $job.destination)) {
            $part = $job.destination + '.part'
            if (-not(Test-Path -LiteralPath $part) -or (Get-Item -LiteralPath $part).Length -ne $job.size) {
                & curl.exe --fail --location --silent --show-error --retry 3 --retry-all-errors --retry-delay 3 --connect-timeout 30 --max-time 5400 --speed-time 120 --speed-limit 1024 --continue-at - --output $part $job.url
                if ($LASTEXITCODE -ne 0) {
                    $mirrorUrl=$job.url.Replace('https://huggingface.co/','https://hf-mirror.com/')
                    & curl.exe --noproxy '*' --fail --location --silent --show-error --retry 3 --retry-all-errors --connect-timeout 30 --max-time 5400 --continue-at - --output $part $mirrorUrl
                    if ($LASTEXITCODE -ne 0) { throw "curl exit $LASTEXITCODE" }
                }
            }
            if ((Get-Item -LiteralPath $part).Length -ne $job.size -or (Get-FileHash -LiteralPath $part).Hash -ne $job.sha256) { throw 'Downloaded size or SHA256 mismatch; partial retained' }
            Move-Item -LiteralPath $part -Destination $job.destination
        }
        if ((Get-Item -LiteralPath $job.destination).Length -ne $job.size -or (Get-FileHash -LiteralPath $job.destination).Hash -ne $job.sha256) { throw 'Existing file integrity mismatch; retained' }
        [pscustomobject]@{name=$job.name; verified=$true; error=$null}
    } catch { [pscustomobject]@{name=$job.name; verified=$false; error=$_.Exception.Message} }
} -ThrottleLimit 2
$results | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath (Join-Path $installRoot 'download-results.json') -Encoding utf8NoBOM
$results | Format-Table -AutoSize
if (@($results | Where-Object { -not $_.verified }).Count) { throw 'Some native model downloads failed; see download-results.json' }
