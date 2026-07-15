[CmdletBinding()]
param(
    [ValidateRange(1, 168)]
    [int]$DurationHours = 24,
    [ValidateRange(15, 3600)]
    [int]$IntervalSeconds = 300,
    [string]$BaseUrl = "http://127.0.0.1:5000",
    [string]$PrometheusUrl = "http://127.0.0.1:9090",
    [string]$GrafanaUrl = "http://127.0.0.1:3000",
    [switch]$RequireLlmMetric,
    [switch]$RequireToolMetric
)

$repoRoot = Split-Path -Parent $PSScriptRoot
$stamp = [DateTime]::UtcNow.ToString("yyyyMMddTHHmmssZ")
$windowRoot = Join-Path $repoRoot ".codex-run\observability-window"
$outputDir = Join-Path $windowRoot $stamp
New-Item -ItemType Directory -Path $outputDir -Force | Out-Null

$pythonArgs = @(
    (Join-Path $PSScriptRoot "observability_window.py"),
    "--duration-hours", $DurationHours,
    "--interval-seconds", $IntervalSeconds,
    "--base-url", $BaseUrl,
    "--prometheus-url", $PrometheusUrl,
    "--grafana-url", $GrafanaUrl,
    "--output-dir", $outputDir,
    "--json"
)
if ($RequireLlmMetric) { $pythonArgs += "--require-llm-metric" }
if ($RequireToolMetric) { $pythonArgs += "--require-tool-metric" }

$stdoutPath = Join-Path $outputDir "monitor.out.log"
$stderrPath = Join-Path $outputDir "monitor.err.log"
$process = Start-Process -FilePath "python" -ArgumentList $pythonArgs -WorkingDirectory $repoRoot -WindowStyle Hidden -RedirectStandardOutput $stdoutPath -RedirectStandardError $stderrPath -PassThru

$latest = [ordered]@{
    status = "started"
    pid = $process.Id
    output_dir = $outputDir
    summary_path = (Join-Path $outputDir "summary.json")
    duration_hours = $DurationHours
    interval_seconds = $IntervalSeconds
}
$latestPath = Join-Path $windowRoot "latest.json"
$latest | ConvertTo-Json | Set-Content -LiteralPath $latestPath -Encoding utf8
$latest | ConvertTo-Json
