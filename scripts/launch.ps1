# Prepare an isolated runtime without changing the system Python or shell profile.
$ErrorActionPreference = "Stop"
$runtimeDir = $env:SCHOLAR_RUNTIME_DIR
if (-not $runtimeDir) {
    $runtimeDir = Join-Path ([Environment]::GetFolderPath("LocalApplicationData")) "scholar-mcp\runtime"
}
$package = $env:SCHOLAR_PACKAGE
$runtimePython = "3.12"
if ($env:PROCESSOR_ARCHITECTURE -eq 'ARM64' -or $env:PROCESSOR_ARCHITEW6432 -eq 'ARM64') {
    $runtimePython = "cpython-3.12-windows-aarch64-none"
}
$constraints = $env:SCHOLAR_WHEEL_INDEX
if (-not $package) {
    $package = "scholar-mcp[rerank]==0.8.5"
    if (-not $constraints) { $constraints = "https://github.com/Liyux3/scholar-mcp/releases/download/v0.8.5/wheel-index.html" }
}
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
            & $shell -NoProfile -ExecutionPolicy Bypass -File $installer | ForEach-Object { [Console]::Error.WriteLine($_) }
            if ($LASTEXITCODE -ne 0) { throw "uv installation failed" }
        } finally {
            $env:UV_UNMANAGED_INSTALL = $previousInstall
            Remove-Item -LiteralPath $installer -ErrorAction SilentlyContinue
        }
    }
}

if ($constraints) {
    & $uvBin tool run --no-config --managed-python --python $runtimePython --find-links $constraints --from $package scholar-mcp @args
} else {
    & $uvBin tool run --no-config --managed-python --python $runtimePython --from $package scholar-mcp @args
}
exit $LASTEXITCODE
