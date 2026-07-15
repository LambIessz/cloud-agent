[CmdletBinding()]
param(
    [string]$BackendUrl = 'http://127.0.0.1:5000',
    [string]$FrontendUrl = 'http://127.0.0.1:5173',
    [int]$TimeoutSeconds = 5,
    [int]$TailLines = 60,
    [switch]$Strict
)

$ErrorActionPreference = 'Stop'

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = Resolve-Path (Join-Path $ScriptDir '..')
$RunDir = Join-Path $RepoRoot '.codex-run'
$FailureCount = 0

function Join-Url {
    param(
        [string]$BaseUrl,
        [string]$Path
    )
    return $BaseUrl.TrimEnd('/') + '/' + $Path.TrimStart('/')
}

function Get-UrlPort {
    param([string]$Url)
    $Uri = [Uri]$Url
    if ($Uri.Port -gt 0) {
        return $Uri.Port
    }
    if ($Uri.Scheme -eq 'https') {
        return 443
    }
    return 80
}

function Write-Check {
    param(
        [string]$Name,
        [bool]$Ok,
        [string]$Detail
    )
    if ($Ok) {
        Write-Host "[PASS] $Name - $Detail"
    }
    else {
        $script:FailureCount += 1
        Write-Host "[FAIL] $Name - $Detail"
    }
}

function Write-Info {
    param(
        [string]$Name,
        [string]$Detail
    )
    Write-Host "[INFO] $Name - $Detail"
}

function Test-Port {
    param(
        [string]$Name,
        [int]$Port
    )
    $Connections = @(Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue)
    if ($Connections.Count -eq 0) {
        Write-Check -Name "$Name port $Port" -Ok $false -Detail "not listening"
        return
    }

    $Processes = $Connections |
        Select-Object -ExpandProperty OwningProcess -Unique |
        ForEach-Object {
            try {
                Get-Process -Id $_ -ErrorAction Stop
            }
            catch {
                $null
            }
        } |
        Where-Object { $null -ne $_ } |
        ForEach-Object { "$($_.ProcessName)#$($_.Id)" }

    $Detail = if ($Processes) { $Processes -join ', ' } else { 'listener process unavailable' }
    Write-Check -Name "$Name port $Port" -Ok $true -Detail $Detail
}

function Test-Http {
    param(
        [string]$Name,
        [string]$Url,
        [string]$Contains = ''
    )
    try {
        $Response = Invoke-WebRequest -UseBasicParsing -Uri $Url -TimeoutSec $TimeoutSeconds
        $Content = [string]$Response.Content
        $MatchesContent = if ($Contains) { $Content.Contains($Contains) } else { $true }
        $Ok = $Response.StatusCode -ge 200 -and $Response.StatusCode -lt 400 -and $MatchesContent
        $Detail = "HTTP $($Response.StatusCode)"
        if ($Contains -and -not $MatchesContent) {
            $Detail += ", missing expected text '$Contains'"
        }
        Write-Check -Name $Name -Ok $Ok -Detail $Detail
    }
    catch {
        Write-Check -Name $Name -Ok $false -Detail $_.Exception.Message
    }
}

function Show-Environment {
    $Names = @(
        'DEEPSEEK_API_KEY',
        'CLOUD_AGENT_CORS_ORIGINS',
        'CLOUD_AGENT_LLM_PRICING_CONFIG',
        'CLOUD_AGENT_SEMANTIC_CACHE_ENABLED',
        'CLOUD_AGENT_VECTOR_SEARCH_ENABLED',
        'CLOUD_AGENT_KNOWLEDGE_GRAPH_ENABLED',
        'CLOUD_AGENT_BACKGROUND_EXTRACT_ENABLED',
        'CLOUD_AGENT_SEMANTIC_CACHE_WRITE_ENABLED'
    )
    foreach ($Name in $Names) {
        $Value = [Environment]::GetEnvironmentVariable($Name)
        if ([string]::IsNullOrWhiteSpace($Value)) {
            Write-Info -Name "env:$Name" -Detail 'unset in current shell'
        }
        elseif ($Name -like '*KEY*' -or $Name -like '*SECRET*') {
            Write-Info -Name "env:$Name" -Detail 'set'
        }
        else {
            Write-Info -Name "env:$Name" -Detail $Value
        }
    }
}

function Show-LogTail {
    param([string]$Path)
    if (Test-Path $Path) {
        Write-Host "[INFO] log tail - $Path"
        Get-Content -Path $Path -Tail $TailLines
    }
    else {
        Write-Info -Name 'log tail' -Detail "missing $Path"
    }
}

$BackendPort = Get-UrlPort $BackendUrl
$FrontendPort = Get-UrlPort $FrontendUrl

Write-Host "[doctor] Cloud Agent local diagnostics"
Write-Host "[doctor] BackendUrl=$BackendUrl"
Write-Host "[doctor] FrontendUrl=$FrontendUrl"

Test-Port -Name 'backend' -Port $BackendPort
Test-Port -Name 'frontend' -Port $FrontendPort
Test-Http -Name 'backend /readyz' -Url (Join-Url $BackendUrl '/readyz') -Contains '"status":"ready"'
Test-Http -Name 'frontend home' -Url $FrontendUrl -Contains '<div id="app">'
Test-Http -Name 'frontend proxy /api/metrics' -Url (Join-Url $FrontendUrl '/api/metrics') -Contains 'cloud_agent'
Show-Environment

Show-LogTail -Path (Join-Path $RunDir 'backend.log')
Show-LogTail -Path (Join-Path $RunDir 'frontend.log')
Show-LogTail -Path (Join-Path $RunDir 'local-smoke-backend.log')
Show-LogTail -Path (Join-Path $RunDir 'local-smoke-frontend.log')

if ($FailureCount -gt 0) {
    Write-Host "[doctor] completed with $FailureCount failing check(s)"
    Write-Host "[doctor] next: run ops/chat_sse_local_smoke.ps1 to start missing services and verify SSE"
    if ($Strict) {
        exit 1
    }
}
else {
    Write-Host "[doctor] all critical checks passed"
}
