# SPDX-License-Identifier: Apache-2.0
# Runs the oss test suite in three pytest processes, so no one process holds the whole suite in memory.
# Usage, from anywhere:  powershell -File oss/tools/suite_split.ps1 [-Python <python.exe>] [-Log <file>]
# Writes each group's summary (every FAILED and ERROR line, then its count) to the log, and its whole output beside it
# (<log>.group<n>.txt), and exits non-zero if any group failed.
param(
    [string]$Python = "",
    [string]$Log = (Join-Path ([System.IO.Path]::GetTempPath()) "g6-suite-split.log")
)
$ErrorActionPreference = "Continue"
$oss = Split-Path -Parent $PSScriptRoot
Set-Location $oss
if (-not $Python) {
    $venv = Join-Path $oss ".venv\Scripts\python.exe"
    if (Test-Path $venv) { $Python = $venv } else { $Python = "python" }
}
Remove-Item $Log, "$Log.group*.txt" -ErrorAction SilentlyContinue
$all = Get-ChildItem tests | Where-Object { $_.Name -ne "__pycache__" -and $_.Name -ne "conftest.py" } |
    ForEach-Object { "tests/" + $_.Name }
$big = @("tests/workforce", "tests/metaharness", "tests/research")
$rest = @($all | Where-Object { $big -notcontains $_ })
$half = [math]::Ceiling($rest.Count / 2)
$groups = @(@($big), @($rest[0..($half - 1)]), @($rest[$half..($rest.Count - 1)]))
$failed = 0
$n = 0
foreach ($g in $groups) {
    $n++
    "== group $n : $($g -join ' ')" | Add-Content $Log
    $out = @(& $Python -m pytest -q -p no:cacheprovider @g 2>&1 | ForEach-Object { "$_" })
    $code = $LASTEXITCODE
    $out | Set-Content -Encoding utf8 "$Log.group$n.txt"   # the group's whole output, tracebacks included
    @($out | Where-Object { $_ -match '^(FAILED|ERROR) ' }) + @($out | Select-Object -Last 1) | Add-Content $Log
    "group $n exit $code" | Add-Content $Log
    if ($code -ne 0) { $failed++ }
}
"done, $failed group(s) failed" | Add-Content $Log
Write-Output "log: $Log"
exit $failed
