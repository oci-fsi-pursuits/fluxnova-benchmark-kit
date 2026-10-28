param([Parameter(ValueFromRemainingArguments=$true)][string[]]$BenchArguments)
$ErrorActionPreference = 'Stop'
$benchmarkPython = if ($env:BENCH_PYTHON) { $env:BENCH_PYTHON } else { 'python' }
Push-Location $PSScriptRoot
try { & $benchmarkPython -m benchlib.cli @BenchArguments; exit $LASTEXITCODE } finally { Pop-Location }
