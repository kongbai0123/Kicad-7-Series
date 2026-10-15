$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path -LiteralPath (Split-Path -Parent $PSScriptRoot)).Path
$runtimePath = Join-Path $PSScriptRoot 'runtime.json'
if (-not (Test-Path -LiteralPath $runtimePath)) { Write-Output 'The learning entry is not running.'; exit 0 }
$runtime = Get-Content -LiteralPath $runtimePath -Raw | ConvertFrom-Json
if ($runtime.project -ne $projectRoot) { throw 'The saved process belongs to another project.' }
$processInfo = Get-CimInstance -ClassName Win32_Process -Filter "ProcessId = $($runtime.pid)" -ErrorAction SilentlyContinue
if ($processInfo) {
    $expectedScript = Join-Path $PSScriptRoot 'server.py'
    if ($processInfo.CommandLine -notlike ('*' + $expectedScript + '*')) { throw 'The saved process is not this learning entry; it was left running.' }
    Stop-Process -Id $runtime.pid
}
Write-Output 'The learning entry has stopped. All progress remains saved.'
