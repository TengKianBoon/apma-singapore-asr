param([Parameter(Mandatory)][string]$RepositoryRoot)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$modulePath = Join-Path $RepositoryRoot 'scripts\windows\APMA_CredentialStore.ps1'
$probePath = Join-Path $RepositoryRoot 'tests\windows\CredentialStoreRestartProbe.ps1'
. $modulePath

$testId = [Guid]::NewGuid().ToString('N')
$meralionTarget = "APMA/Test/$testId/MERALION"
$geminiTarget = "APMA/Test/$testId/GEMINI"
$dashScopeTarget = "APMA/Test/$testId/DASHSCOPE"
$ossAccessKeyIdTarget = "APMA/Test/$testId/OSS_ID"
$ossAccessKeySecretTarget = "APMA/Test/$testId/OSS_SECRET"
$ossEndpointTarget = "APMA/Test/$testId/OSS_ENDPOINT"
$ossBucketTarget = "APMA/Test/$testId/OSS_BUCKET"
$meralionSecret = "fake-$([Guid]::NewGuid().ToString('N'))"
$geminiSecret = "fake-$([Guid]::NewGuid().ToString('N'))"
$dashScopeSecret = "fake-$([Guid]::NewGuid().ToString('N'))"
$ossIdSecret = "fake-$([Guid]::NewGuid().ToString('N'))"
$ossKeySecret = "fake-$([Guid]::NewGuid().ToString('N'))"
$ossEndpointSecret = 'https://oss-ap-southeast-1.aliyuncs.com'
$ossBucketSecret = "fake-$([Guid]::NewGuid().ToString('N'))"
$statusArgs = @{
    MeralionTarget = $meralionTarget
    GeminiTarget = $geminiTarget
    DashScopeTarget = $dashScopeTarget
    OssAccessKeyIdTarget = $ossAccessKeyIdTarget
    OssAccessKeySecretTarget = $ossAccessKeySecretTarget
    OssEndpointTarget = $ossEndpointTarget
    OssBucketTarget = $ossBucketTarget
}
$allFakeSecrets = @(
    $meralionSecret, $geminiSecret, $dashScopeSecret, $ossIdSecret,
    $ossKeySecret, $ossBucketSecret
)
$messages = [Collections.Generic.List[string]]::new()

function Get-TextSha256 {
    param([Parameter(Mandatory)][string]$Value)
    $bytes = [Text.Encoding]::UTF8.GetBytes($Value)
    try {
        $algorithm = [Security.Cryptography.SHA256]::Create()
        try {
            $digest = $algorithm.ComputeHash($bytes)
            return -join ($digest | ForEach-Object { $_.ToString('x2') })
        }
        finally {
            $algorithm.Dispose()
        }
    }
    finally {
        [Array]::Clear($bytes, 0, $bytes.Length)
    }
}

try {
    $missing = Get-ApmaCredentialStatus @statusArgs
    if ($missing.MeralionConfigured -or $missing.GeminiConfigured -or $missing.QwenFiletransConfigured) {
        throw 'Fresh fake credential targets unexpectedly existed.'
    }
    $messages.Add('startup_without_credentials=clear_missing_state')

    $null = Set-ApmaCredential -Target $meralionTarget -Secret $meralionSecret
    $null = Set-ApmaCredential -Target $geminiTarget -Secret $geminiSecret
    $null = Set-ApmaCredential -Target $dashScopeTarget -Secret $dashScopeSecret
    $null = Set-ApmaCredential -Target $ossAccessKeyIdTarget -Secret $ossIdSecret
    $null = Set-ApmaCredential -Target $ossAccessKeySecretTarget -Secret $ossKeySecret
    $null = Set-ApmaCredential -Target $ossEndpointTarget -Secret $ossEndpointSecret
    $null = Set-ApmaCredential -Target $ossBucketTarget -Secret $ossBucketSecret
    $configured = Get-ApmaCredentialStatus @statusArgs
    if (-not ($configured.MeralionConfigured -and $configured.GeminiConfigured -and $configured.QwenFiletransConfigured)) {
        throw 'Secure credential existence check failed.'
    }
    if ((Get-ApmaCredential -Target $meralionTarget) -cne $meralionSecret) {
        throw 'Secure MERaLiON read did not match the fake value.'
    }
    if ((Get-ApmaCredential -Target $geminiTarget) -cne $geminiSecret) {
        throw 'Secure Gemini read did not match the fake value.'
    }
    $messages.Add('secure_write_read_exists=pass')

    $expectedHash = Get-TextSha256 -Value $meralionSecret
    $restartHash = & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $probePath `
        -ModulePath $modulePath -Target $meralionTarget
    if ([string]$restartHash.Trim() -cne $expectedHash) {
        throw 'A new PowerShell process did not retrieve the same fake credential.'
    }
    $messages.Add('restart_reopen=pass')

    $tracked = & git -C $RepositoryRoot ls-files
    foreach ($relativePath in $tracked) {
        $path = Join-Path $RepositoryRoot $relativePath
        if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { continue }
        $content = [IO.File]::ReadAllText($path)
        if ($allFakeSecrets | Where-Object { $content.Contains($_) }) {
            throw 'A generated fake secret appeared in a Git-tracked file.'
        }
    }
    $messages.Add('git_artifact_secret_scan=pass')
}
finally {
    $null = Remove-ApmaCredential -Target $meralionTarget
    $null = Remove-ApmaCredential -Target $geminiTarget
    $null = Remove-ApmaCredential -Target $dashScopeTarget
    $null = Remove-ApmaCredential -Target $ossAccessKeyIdTarget
    $null = Remove-ApmaCredential -Target $ossAccessKeySecretTarget
    $null = Remove-ApmaCredential -Target $ossEndpointTarget
    $null = Remove-ApmaCredential -Target $ossBucketTarget
}

$deleted = Get-ApmaCredentialStatus @statusArgs
if ($deleted.MeralionConfigured -or $deleted.GeminiConfigured -or $deleted.QwenFiletransConfigured) {
    throw 'Fake credentials remained after deletion.'
}
$messages.Add('forget_delete=pass')

$logText = $messages -join "`n"
if ($allFakeSecrets | Where-Object { $logText.Contains($_) }) {
    throw 'A fake secret appeared in self-test output.'
}
$messages.Add('logs_secret_free=pass')
$messages
