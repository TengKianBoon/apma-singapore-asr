param(
    [Parameter(Mandatory)][string]$ModulePath,
    [Parameter(Mandatory)][string]$Target
)

$ErrorActionPreference = 'Stop'
. $ModulePath
$value = Get-ApmaCredential -Target $Target
if ([string]::IsNullOrWhiteSpace($value)) {
    throw 'Credential was not available after process restart.'
}
$bytes = [Text.Encoding]::UTF8.GetBytes($value)
try {
    $algorithm = [Security.Cryptography.SHA256]::Create()
    try {
        $digest = $algorithm.ComputeHash($bytes)
        -join ($digest | ForEach-Object { $_.ToString('x2') })
    }
    finally {
        $algorithm.Dispose()
    }
}
finally {
    [Array]::Clear($bytes, 0, $bytes.Length)
    $value = $null
}
