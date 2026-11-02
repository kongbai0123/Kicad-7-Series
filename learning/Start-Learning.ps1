param([switch]$NoBrowser)
$ErrorActionPreference = 'Stop'
$learningRoot = $PSScriptRoot
$projectRoot = (Resolve-Path -LiteralPath (Split-Path -Parent $learningRoot)).Path
$serverScript = Join-Path $learningRoot 'server.py'
$runtimePath = Join-Path $learningRoot 'runtime.json'
$selectedPort = $null

foreach ($candidatePort in 8765..8774) {
    try {
        $health = Invoke-RestMethod -Uri "http://127.0.0.1:$candidatePort/api/health" -TimeoutSec 1
        if ($health.application -eq 'kicad-guided-learning' -and $health.project -eq $projectRoot -and $health.codeCurrent -ne $true -and $health.pid) {
            # The running service predates the current code; replace it so new features are served.
            Stop-Process -Id ([int]$health.pid) -ErrorAction SilentlyContinue
            Start-Sleep -Milliseconds 500
        } elseif ($health.application -eq 'kicad-guided-learning' -and $health.project -eq $projectRoot) {
            $entryUrl = "http://127.0.0.1:$candidatePort/"
            if (-not $NoBrowser) { Start-Process $entryUrl }
            Write-Output $entryUrl
            exit 0
        }
    } catch { }
    $listener = New-Object System.Net.Sockets.TcpListener([System.Net.IPAddress]::Loopback, $candidatePort)
    try {
        $listener.Start()
        $selectedPort = $candidatePort
    } catch { } finally { $listener.Stop() }
    if ($null -ne $selectedPort) { break }
}
if ($null -eq $selectedPort) { throw 'Ports 8765-8774 are occupied. Please close an unused local service and retry.' }

$pythonCommand = Get-Command python.exe -ErrorAction SilentlyContinue
$pythonPath = $null
$pythonPrefix = @()
if ($pythonCommand -and $pythonCommand.Source -notlike '*WindowsApps*') {
    $pythonPath = $pythonCommand.Source
} else {
    $bundledPython = Join-Path $env:USERPROFILE '.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
    if (Test-Path -LiteralPath $bundledPython) {
        $pythonPath = $bundledPython
    } else {
        $pythonLauncher = Get-Command py.exe -ErrorAction SilentlyContinue
        if ($pythonLauncher) { $pythonPath = $pythonLauncher.Source; $pythonPrefix = @('-3') }
    }
}
if (-not $pythonPath) { throw 'Python 3.10 or newer is required. Ask the tutor to check the local Python installation.' }
$versionArgs = $pythonPrefix + @('-c', 'import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)')
& $pythonPath @versionArgs
if ($LASTEXITCODE -ne 0) { throw 'Python 3.10 or newer is required.' }
$serverArgs = $pythonPrefix + @('"' + $serverScript + '"', '--port', [string]$selectedPort)
$serverProcess = Start-Process -FilePath $pythonPath -ArgumentList $serverArgs -WorkingDirectory $projectRoot -WindowStyle Hidden -RedirectStandardOutput (Join-Path $learningRoot 'server.log') -RedirectStandardError (Join-Path $learningRoot 'server-error.log') -PassThru
@{pid=$serverProcess.Id; port=$selectedPort; project=$projectRoot; script=$serverScript} | ConvertTo-Json | Set-Content -LiteralPath $runtimePath -Encoding utf8
$entryUrl = "http://127.0.0.1:$selectedPort/"
$ready = $false
foreach ($attempt in 1..24) {
    Start-Sleep -Milliseconds 250
    try {
        $health = Invoke-RestMethod -Uri ($entryUrl + 'api/health') -TimeoutSec 1
        if ($health.application -eq 'kicad-guided-learning' -and $health.project -eq $projectRoot) { $ready = $true; break }
    } catch { }
    if ($serverProcess.HasExited) { break }
}
if (-not $ready) { throw 'The learning entry could not start. See learning/server-error.log.' }
if (-not $NoBrowser) { Start-Process $entryUrl }
Write-Output $entryUrl
