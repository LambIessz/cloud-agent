[CmdletBinding()]
param(
    [string]$BackendUrl = 'http://127.0.0.1:5000',
    [string]$FrontendUrl = 'http://127.0.0.1:5173',
    [int]$TimeoutSeconds = 90,
    [switch]$KeepRunning
)

$ErrorActionPreference = 'Stop'

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = Resolve-Path (Join-Path $ScriptDir '..')
$RunDir = Join-Path $RepoRoot '.codex-run'
$BackendLog = Join-Path $RunDir 'local-smoke-backend.log'
$FrontendLog = Join-Path $RunDir 'local-smoke-frontend.log'
$StartedProcesses = New-Object System.Collections.Generic.List[System.Diagnostics.Process]

New-Item -ItemType Directory -Force -Path $RunDir | Out-Null

function ConvertTo-SingleQuotedLiteral {
    param([string]$Value)
    return "'" + ($Value -replace "'", "''") + "'"
}

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

function Test-PortListening {
    param([int]$Port)
    $Connection = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
        Select-Object -First 1
    return $null -ne $Connection
}

function Wait-HttpReady {
    param(
        [string]$Url,
        [string]$Name,
        [int]$TimeoutSeconds
    )
    $Deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    $LastError = $null
    while ((Get-Date) -lt $Deadline) {
        try {
            $Response = Invoke-WebRequest -UseBasicParsing -Uri $Url -TimeoutSec 3
            if ($Response.StatusCode -ge 200 -and $Response.StatusCode -lt 500) {
                Write-Host "[local-sse-smoke] $Name ready: $Url"
                return
            }
        }
        catch {
            $LastError = $_.Exception.Message
        }
        Start-Sleep -Seconds 2
    }
    throw "$Name did not become ready at $Url. Last error: $LastError"
}

function Start-Backend {
    param([int]$Port)

    $AppDir = Join-Path $RepoRoot 'cloud_agent\app'
    $PricingConfig = Join-Path $RepoRoot 'ops\prometheus\llm_pricing.example.yml'
    $AppDirLiteral = ConvertTo-SingleQuotedLiteral $AppDir
    $BackendLogLiteral = ConvertTo-SingleQuotedLiteral $BackendLog
    $PricingLiteral = ConvertTo-SingleQuotedLiteral $PricingConfig
    $CorsOriginsLiteral = ConvertTo-SingleQuotedLiteral $FrontendUrl

    $Command = @"
`$env:PYTHONIOENCODING='utf-8'
`$env:PYTHONUTF8='1'
`$env:HF_ENDPOINT='https://hf-mirror.com'
`$env:HF_HUB_DISABLE_SYMLINKS_WARNING='1'
`$env:CLOUD_AGENT_LLM_PRICING_CONFIG=$PricingLiteral
`$env:CLOUD_AGENT_CORS_ORIGINS=$CorsOriginsLiteral
`$env:CLOUD_AGENT_SEMANTIC_CACHE_ENABLED='false'
`$env:CLOUD_AGENT_LONG_TERM_MEMORY_ENABLED='false'
`$env:CLOUD_AGENT_VECTOR_SEARCH_ENABLED='false'
`$env:CLOUD_AGENT_KNOWLEDGE_GRAPH_ENABLED='false'
`$env:CLOUD_AGENT_BACKGROUND_EXTRACT_ENABLED='false'
`$env:CLOUD_AGENT_SEMANTIC_CACHE_WRITE_ENABLED='false'
Set-Location $AppDirLiteral
python -X utf8 -m uvicorn app_main:app --host 0.0.0.0 --port $Port *> $BackendLogLiteral
"@

    Write-Host "[local-sse-smoke] starting backend, log: $BackendLog"
    return Start-Process -FilePath powershell -ArgumentList @(
        '-NoProfile',
        '-ExecutionPolicy',
        'Bypass',
        '-Command',
        $Command
    ) -WindowStyle Hidden -PassThru
}

function Start-Frontend {
    param([int]$Port)

    $FrontendDir = Join-Path $RepoRoot 'cloud_agent\front\cloud_agent'
    $FrontendDirLiteral = ConvertTo-SingleQuotedLiteral $FrontendDir
    $FrontendLogLiteral = ConvertTo-SingleQuotedLiteral $FrontendLog

    $Command = @"
Set-Location $FrontendDirLiteral
npm run dev -- --host 127.0.0.1 --port $Port --strictPort *> $FrontendLogLiteral
"@

    Write-Host "[local-sse-smoke] starting frontend, log: $FrontendLog"
    return Start-Process -FilePath powershell -ArgumentList @(
        '-NoProfile',
        '-ExecutionPolicy',
        'Bypass',
        '-Command',
        $Command
    ) -WindowStyle Hidden -PassThru
}

function Stop-ProcessTree {
    param([int]$ProcessId)
    Get-CimInstance Win32_Process -Filter "ParentProcessId = $ProcessId" -ErrorAction SilentlyContinue |
        ForEach-Object { Stop-ProcessTree -ProcessId $_.ProcessId }
    Stop-Process -Id $ProcessId -Force -ErrorAction SilentlyContinue
}

try {
    $BackendPort = Get-UrlPort $BackendUrl
    if (Test-PortListening $BackendPort) {
        Write-Host "[local-sse-smoke] reusing backend on port $BackendPort"
    }
    else {
        $Process = Start-Backend -Port $BackendPort
        $StartedProcesses.Add($Process)
    }

    Wait-HttpReady -Url (Join-Url $BackendUrl '/readyz') -Name 'backend' -TimeoutSeconds $TimeoutSeconds

    $FrontendPort = Get-UrlPort $FrontendUrl
    if (Test-PortListening $FrontendPort) {
        Write-Host "[local-sse-smoke] reusing frontend on port $FrontendPort"
    }
    else {
        $Process = Start-Frontend -Port $FrontendPort
        $StartedProcesses.Add($Process)
    }

    Wait-HttpReady -Url $FrontendUrl -Name 'frontend' -TimeoutSeconds $TimeoutSeconds

    Push-Location $RepoRoot
    try {
        python ops/chat_sse_smoke.py `
            --backend-url $BackendUrl `
            --frontend-url $FrontendUrl `
            --timeout $TimeoutSeconds
        if ($LASTEXITCODE -ne 0) {
            throw "chat_sse_smoke.py failed with exit code $LASTEXITCODE"
        }
    }
    finally {
        Pop-Location
    }
}
finally {
    if ($KeepRunning) {
        if ($StartedProcesses.Count -gt 0) {
            $Ids = ($StartedProcesses | ForEach-Object { $_.Id }) -join ', '
            Write-Host "[local-sse-smoke] keeping started processes running: $Ids"
        }
    }
    else {
        foreach ($Process in $StartedProcesses) {
            Write-Host "[local-sse-smoke] stopping process tree $($Process.Id)"
            Stop-ProcessTree -ProcessId $Process.Id
        }
    }
}
