param([switch]$NoBrowser)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$repositoryRoot = $PSScriptRoot
$credentialScript = Join-Path $repositoryRoot 'scripts\windows\APMA_CredentialStore.ps1'
$setupScript = Join-Path $repositoryRoot 'scripts\windows\Configure_APMA_Credentials.ps1'
$healthUrl = 'http://127.0.0.1:8000/api/health'
$dashboardUrl = 'http://127.0.0.1:8000/'
$containerName = 'apma-v1-dashboard'
$imageName = 'apma-v5:qwen-filetrans'

. $credentialScript

function Test-ApmaDashboard {
    try {
        $health = Invoke-RestMethod -Uri $healthUrl -TimeoutSec 3
        return [bool]$health.apma_running
    }
    catch {
        return $false
    }
}

function Test-DockerBackend {
    if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
        return $false
    }
    & docker info *> $null
    return $LASTEXITCODE -eq 0
}

function Wait-DockerBackend {
    param([int]$Seconds = 90)
    $deadline = [DateTime]::UtcNow.AddSeconds($Seconds)
    while ([DateTime]::UtcNow -lt $deadline) {
        if (Test-DockerBackend) { return $true }
        Start-Sleep -Seconds 3
    }
    return $false
}

function Wait-ApmaDashboard {
    param([int]$Seconds = 60)
    $deadline = [DateTime]::UtcNow.AddSeconds($Seconds)
    while ([DateTime]::UtcNow -lt $deadline) {
        if (Test-ApmaDashboard) { return $true }
        Start-Sleep -Seconds 2
    }
    return $false
}

if (Test-ApmaDashboard) {
    if (-not $NoBrowser) { Start-Process $dashboardUrl }
    Write-Host 'APMA is already running.' -ForegroundColor Green
    exit 0
}

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    Write-Host 'Docker is not installed or is not available in PATH.' -ForegroundColor Red
    Write-Host 'Install Docker Desktop, then run Start APMA again.'
    exit 1
}

if (-not (Test-DockerBackend)) {
    $dockerDesktop = Join-Path $env:ProgramFiles 'Docker\Docker\Docker Desktop.exe'
    if (Test-Path -LiteralPath $dockerDesktop) {
        Write-Host 'Starting Docker Desktop. Please wait...'
        Start-Process -FilePath $dockerDesktop -WindowStyle Hidden
        if (-not (Wait-DockerBackend)) {
            Write-Host 'Start Docker Desktop, then run Start APMA again.' -ForegroundColor Yellow
            exit 1
        }
    }
    else {
        Write-Host 'Start Docker Desktop, then run Start APMA again.' -ForegroundColor Yellow
        exit 1
    }
}

$credentialStatus = Get-ApmaCredentialStatus
if (-not (
    $credentialStatus.MeralionConfigured -or
    $credentialStatus.GeminiConfigured -or
    $credentialStatus.DashScopeConfigured
)) {
    Write-Host 'No optional external-provider keys are configured. Opening secure setup...'
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $setupScript
    $credentialStatus = Get-ApmaCredentialStatus
}

$meralionSecret = Get-ApmaCredential -Target 'APMA/MERALION_API_KEY'
$env:MERALION_API_KEY = $meralionSecret
$geminiSecret = Get-ApmaCredential -Target 'APMA/GEMINI_API_KEY'
$env:GEMINI_API_KEY = $geminiSecret
$qwenStagingMode = [Environment]::GetEnvironmentVariable(
    'QWEN_FILETRANS_STAGING_MODE',
    'Process'
)
if ([string]::IsNullOrWhiteSpace($qwenStagingMode)) {
    $qwenStagingMode = 'data_uri'
}
$qwenCredentialStatusNames = [ordered]@{
    'DASHSCOPE_API_KEY' = 'DashScopeConfigured'
    'ALIYUN_OSS_ACCESS_KEY_ID' = 'OssAccessKeyIdConfigured'
    'ALIYUN_OSS_ACCESS_KEY_SECRET' = 'OssAccessKeySecretConfigured'
    'ALIYUN_OSS_ENDPOINT' = 'OssEndpointConfigured'
    'ALIYUN_OSS_BUCKET' = 'OssBucketConfigured'
}
$qwenSecretNames = @()
foreach ($entry in $qwenCredentialStatusNames.GetEnumerator()) {
    if ($credentialStatus.($entry.Value)) {
        $qwenSecretNames += $entry.Key
        $value = Get-ApmaCredential -Target "APMA/$($entry.Key)"
        [Environment]::SetEnvironmentVariable($entry.Key, $value, 'Process')
        $value = $null
    }
}

try {
    & docker image inspect $imageName *> $null
    if ($LASTEXITCODE -ne 0) {
        Write-Host 'Preparing APMA for first use. This can take a few minutes...'
        & docker build -t $imageName $repositoryRoot
        if ($LASTEXITCODE -ne 0) { throw 'APMA Docker image could not be prepared.' }
    }

    $existingContainer = & docker ps -a --filter "name=^/$containerName$" --format '{{.Names}}'
    if ($existingContainer -eq $containerName) {
        & docker rm -f $containerName *> $null
    }

    $qwenDockerArguments = @()
    foreach ($name in $qwenSecretNames) {
        $qwenDockerArguments += @('-e', $name)
    }
    $arguments = @(
        'run', '-d', '--rm', '--name', $containerName,
        '-w', '/app', '-p', '8000:8000',
        '-e', 'DRY_RUN=false',
        '-e', 'ENABLE_LIVE_OPENAI_TRANSCRIPTION=true',
        '-e', 'ENABLE_LIVE_OPENAI_MINUTES=true',
        '-e', 'ENABLE_LIVE_OPENAI_RECONCILIATION=true',
        '-e', 'ENABLE_LIVE_MERALION_TRANSCRIPTION=true',
        '-e', 'ENABLE_LIVE_GEMINI_TRANSCRIPTION=true',
        '-e', 'ENABLE_LIVE_QWEN_FILETRANS_TRANSCRIPTION=true',
        '-e', "QWEN_FILETRANS_STAGING_MODE=$qwenStagingMode",
        '-e', 'MAX_COST_PER_JOB_USD=5.00',
        '-e', 'MAX_MINUTES_COST_PER_JOB_USD=2.00',
        '-e', "APMA_HOST_JOBS_PATH=$(Join-Path $repositoryRoot 'jobs')",
        '-e', 'OPENAI_API_KEY',
        '-e', 'MERALION_API_KEY',
        '-e', 'GEMINI_API_KEY'
    ) + $qwenDockerArguments + @(
        '-v', "${repositoryRoot}:/app",
        $imageName, 'python', 'scripts/local_dashboard.py'
    )
    & docker @arguments | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'APMA dashboard container did not start.' }
}
finally {
    Remove-Item Env:MERALION_API_KEY -ErrorAction SilentlyContinue
    Remove-Item Env:GEMINI_API_KEY -ErrorAction SilentlyContinue
    foreach ($name in $qwenSecretNames) {
        Remove-Item "Env:$name" -ErrorAction SilentlyContinue
    }
    $meralionSecret = $null
    $geminiSecret = $null
}

if (-not (Wait-ApmaDashboard)) {
    Write-Host 'APMA did not become ready. Check Docker Desktop, then run Start APMA again.' -ForegroundColor Red
    exit 1
}

Write-Host 'APMA is running.' -ForegroundColor Green
Write-Host $dashboardUrl
if (-not $NoBrowser) { Start-Process $dashboardUrl }
exit 0
