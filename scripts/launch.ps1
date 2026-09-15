# Prepare an isolated runtime without changing the system Python or shell profile.
$ErrorActionPreference = "Stop"
$runtimeDir = $env:SCHOLAR_RUNTIME_DIR
if (-not $runtimeDir) {
    $runtimeDir = Join-Path ([Environment]::GetFolderPath("LocalApplicationData")) "scholar-mcp\runtime"
}
$package = $env:SCHOLAR_PACKAGE
if (-not $package) { $package = "scholar-mcp[rerank]" }
$uvBin = $env:SCHOLAR_UV
if (-not $uvBin) {
    $existing = Get-Command uv -CommandType Application -ErrorAction SilentlyContinue
    if ($existing) { $uvBin = $existing.Source }
}
if (-not $uvBin) {
    $uvBin = Join-Path $runtimeDir "uv\uv.exe"
    if (-not (Test-Path -LiteralPath $uvBin)) {
        $installer = Join-Path ([IO.Path]::GetTempPath()) ("scholar-uv-" + [Guid]::NewGuid().ToString("N") + ".ps1")
        $previousInstall = $env:UV_UNMANAGED_INSTALL
        try {
            Invoke-WebRequest -Uri "https://astral.sh/uv/install.ps1" -OutFile $installer
            $env:UV_UNMANAGED_INSTALL = Join-Path $runtimeDir "uv"
            # Use a child process so its installer settings cannot leak to the caller.
            $shell = (Get-Process -Id $PID).Path
            & $shell -NoProfile -File $installer | ForEach-Object { [Console]::Error.WriteLine($_) }
            if ($LASTEXITCODE -ne 0) { throw "uv installation failed" }
        } finally {
            $env:UV_UNMANAGED_INSTALL = $previousInstall
            Remove-Item -LiteralPath $installer -ErrorAction SilentlyContinue
        }
    }
}

& $uvBin tool run --no-config --managed-python --python 3.12 --from $package scholar-mcp @args
exit $LASTEXITCODE
