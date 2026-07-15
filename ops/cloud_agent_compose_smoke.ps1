[CmdletBinding()]
param(
    [string]$EnvFile = 'ops/cloud_agent.env',
    [string]$ComposeFile = 'ops/docker-compose.cloud-agent.yml',
    [string]$BackendUrl = 'http://127.0.0.1:5000',
    [int]$TimeoutSeconds = 180,
    [switch]$KeepRunning
)

$ErrorActionPreference = 'Stop'

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = Resolve-Path (Join-Path $ScriptDir '..')
$RunDir = Join-Path $RepoRoot '.codex-run'
$DoctorJson = Join-Path $RunDir 'compose-doctor.json'
$ComposePsLog = Join-Path $RunDir 'compose-ps.log'
$ComposeAppLog = Join-Path $RunDir 'compose-cloud-agent.log'
$ComposeAllLog = Join-Path $RunDir 'compose-all.log'
$ManagedServices = @('cloud_agent', 'redis', 'mysql', 'neo4j')
$PreExistingServiceIds = @{}

New-Item -ItemType Directory -Force -Path $RunDir | Out-Null

function Join-Url {
    param(
        [string]$BaseUrl,
        [string]$Path
    )
    return $BaseUrl.TrimEnd('/') + '/' + $Path.TrimStart('/')
}

function Resolve-RepoPath {
    param([string]$Path)
    if ([System.IO.Path]::IsPathRooted($Path)) {
        return $Path
    }
    return Join-Path $RepoRoot $Path
}

function Invoke-Compose {
    param([string[]]$ComposeArgs)
    $EnvFilePath = Resolve-RepoPath $EnvFile
    $ComposeFilePath = Resolve-RepoPath $ComposeFile
    & docker compose --env-file $EnvFilePath -f $ComposeFilePath @ComposeArgs
    if ($LASTEXITCODE -ne 0) {
        throw "docker compose $($ComposeArgs -join ' ') failed with exit code $LASTEXITCODE"
    }
}

function Get-ComposeServiceId {
    param([string]$Service)

    $RawIds = @(Invoke-Compose -ComposeArgs @('ps', '-q', $Service))
    $Ids = @(
        $RawIds |
            ForEach-Object { $_.ToString().Trim() } |
            Where-Object { $_ }
    )
    if ($Ids.Count -gt 1) {
        throw "expected at most one compose container for service $Service"
    }
    if ($Ids.Count -eq 1) {
        return $Ids[0]
    }
    return $null
}

function Wait-HttpReady {
    param(
        [string]$Url,
        [int]$TimeoutSeconds
    )
    $Deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    $LastError = $null
    while ((Get-Date) -lt $Deadline) {
        try {
            $Response = Invoke-WebRequest -UseBasicParsing -Uri $Url -TimeoutSec 5
            if ($Response.StatusCode -ge 200 -and $Response.StatusCode -lt 500) {
                Write-Host "[compose-smoke] backend ready: $Url"
                return
            }
        }
        catch {
            $LastError = $_.Exception.Message
        }
        Start-Sleep -Seconds 3
    }
    throw "backend did not become ready at $Url. Last error: $LastError"
}

function Save-ComposeDiagnostics {
    try {
        Invoke-Compose -ComposeArgs @('ps') *> $ComposePsLog
    }
    catch {
        Write-Host "[compose-smoke] failed to collect compose ps: $($_.Exception.Message)"
    }

    try {
        Write-Host "[compose-smoke] collecting diagnostics with docker compose logs --no-color"
        Invoke-Compose -ComposeArgs @('logs', '--no-color', '--tail=200', 'cloud_agent') *> $ComposeAppLog
    }
    catch {
        Write-Host "[compose-smoke] failed to collect cloud_agent logs: $($_.Exception.Message)"
    }

    try {
        Invoke-Compose -ComposeArgs @('logs', '--no-color', '--tail=200') *> $ComposeAllLog
    }
    catch {
        Write-Host "[compose-smoke] failed to collect compose logs: $($_.Exception.Message)"
    }
}

function Stop-NewComposeServices {
    $NewServices = @()
    foreach ($Service in $ManagedServices) {
        if ($PreExistingServiceIds.ContainsKey($Service)) {
            continue
        }
        if (Get-ComposeServiceId -Service $Service) {
            $NewServices += $Service
        }
    }

    if ($NewServices.Count -eq 0) {
        return
    }

    Write-Host "[compose-smoke] stopping only smoke-started services: $($NewServices -join ', ')"
    Invoke-Compose -ComposeArgs (@('stop') + $NewServices)
    Invoke-Compose -ComposeArgs (@('rm', '-f') + $NewServices)
}

Push-Location $RepoRoot
try {
    $EnvFilePath = Resolve-RepoPath $EnvFile
    if (-not (Test-Path $EnvFilePath)) {
        throw "env file not found: $EnvFilePath"
    }

    foreach ($Service in $ManagedServices) {
        $ExistingId = Get-ComposeServiceId -Service $Service
        if ($ExistingId) {
            $PreExistingServiceIds[$Service] = $ExistingId
        }
    }

    Write-Host "[compose-smoke] validating compose config"
    Invoke-Compose -ComposeArgs @('config', '--quiet')

    Write-Host "[compose-smoke] starting cloud_agent stack: docker compose up -d --build"
    Invoke-Compose -ComposeArgs @('up', '-d', '--build')

    Wait-HttpReady -Url (Join-Url $BackendUrl '/readyz') -TimeoutSeconds $TimeoutSeconds

    Write-Host "[compose-smoke] running deployment doctor"
    python ops/cloud_agent_doctor.py `
        --env-file $EnvFile `
        --base-url $BackendUrl `
        --json | Tee-Object -FilePath $DoctorJson
    if ($LASTEXITCODE -ne 0) {
        throw "cloud_agent_doctor.py failed with exit code $LASTEXITCODE"
    }
}
finally {
    Save-ComposeDiagnostics
    if ($KeepRunning) {
        Write-Host "[compose-smoke] keeping compose services running"
    }
    elseif ($PreExistingServiceIds.Count -gt 0) {
        Write-Host "[compose-smoke] preserving pre-existing compose services"
        try {
            Stop-NewComposeServices
        }
        catch {
            Write-Host "[compose-smoke] selective cleanup failed: $($_.Exception.Message)"
        }
    }
    else {
        Write-Host "[compose-smoke] stopping compose services"
        try {
            Invoke-Compose -ComposeArgs @('down')
        }
        catch {
            Write-Host "[compose-smoke] docker compose down failed: $($_.Exception.Message)"
        }
    }
    Pop-Location
}
