Set-StrictMode -Version Latest

if (-not ('APMA.WindowsCredentialNative' -as [type])) {
    Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;

namespace APMA {
    public static class WindowsCredentialNative {
        [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
        public struct Credential {
            public UInt32 Flags;
            public UInt32 Type;
            public string TargetName;
            public string Comment;
            public System.Runtime.InteropServices.ComTypes.FILETIME LastWritten;
            public UInt32 CredentialBlobSize;
            public IntPtr CredentialBlob;
            public UInt32 Persist;
            public UInt32 AttributeCount;
            public IntPtr Attributes;
            public string TargetAlias;
            public string UserName;
        }

        [DllImport("advapi32.dll", EntryPoint = "CredWriteW", CharSet = CharSet.Unicode, SetLastError = true)]
        public static extern bool CredWrite(ref Credential credential, UInt32 flags);

        [DllImport("advapi32.dll", EntryPoint = "CredReadW", CharSet = CharSet.Unicode, SetLastError = true)]
        public static extern bool CredRead(string target, UInt32 type, UInt32 flags, out IntPtr credential);

        [DllImport("advapi32.dll", EntryPoint = "CredDeleteW", CharSet = CharSet.Unicode, SetLastError = true)]
        public static extern bool CredDelete(string target, UInt32 type, UInt32 flags);

        [DllImport("advapi32.dll")]
        public static extern void CredFree(IntPtr credential);
    }
}
'@
}

$script:ApmaCredentialTypeGeneric = [uint32]1
$script:ApmaCredentialPersistLocalMachine = [uint32]2

function Assert-ApmaCredentialTarget {
    param([Parameter(Mandatory)][string]$Target)
    if (-not $Target.StartsWith('APMA/', [StringComparison]::Ordinal)) {
        throw 'APMA credential targets must begin with APMA/.'
    }
}

function Set-ApmaCredential {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][string]$Target,
        [Parameter(Mandatory)][string]$Secret
    )
    Assert-ApmaCredentialTarget -Target $Target
    if ([string]::IsNullOrWhiteSpace($Secret)) {
        throw 'Credential value cannot be blank.'
    }
    $bytes = [Text.Encoding]::Unicode.GetBytes($Secret)
    if ($bytes.Length -gt 512) {
        throw 'Credential value is too long for Windows Credential Manager.'
    }
    $blob = [Runtime.InteropServices.Marshal]::AllocHGlobal($bytes.Length)
    try {
        [Runtime.InteropServices.Marshal]::Copy($bytes, 0, $blob, $bytes.Length)
        $credential = New-Object APMA.WindowsCredentialNative+Credential
        $credential.Type = $script:ApmaCredentialTypeGeneric
        $credential.TargetName = $Target
        $credential.CredentialBlobSize = [uint32]$bytes.Length
        $credential.CredentialBlob = $blob
        $credential.Persist = $script:ApmaCredentialPersistLocalMachine
        $credential.UserName = [Environment]::UserName
        $credential.Comment = 'APMA provider credential for the current Windows user'
        if (-not [APMA.WindowsCredentialNative]::CredWrite([ref]$credential, 0)) {
            throw [ComponentModel.Win32Exception]::new([Runtime.InteropServices.Marshal]::GetLastWin32Error())
        }
        return $true
    }
    finally {
        for ($index = 0; $index -lt $bytes.Length; $index++) {
            [Runtime.InteropServices.Marshal]::WriteByte($blob, $index, 0)
        }
        [Runtime.InteropServices.Marshal]::FreeHGlobal($blob)
        [Array]::Clear($bytes, 0, $bytes.Length)
    }
}

function Get-ApmaCredential {
    [CmdletBinding()]
    param([Parameter(Mandatory)][string]$Target)
    Assert-ApmaCredentialTarget -Target $Target
    $pointer = [IntPtr]::Zero
    if (-not [APMA.WindowsCredentialNative]::CredRead(
        $Target,
        $script:ApmaCredentialTypeGeneric,
        0,
        [ref]$pointer
    )) {
        $errorCode = [Runtime.InteropServices.Marshal]::GetLastWin32Error()
        if ($errorCode -eq 1168) {
            return $null
        }
        throw [ComponentModel.Win32Exception]::new($errorCode)
    }
    try {
        $credential = [Runtime.InteropServices.Marshal]::PtrToStructure(
            $pointer,
            [type][APMA.WindowsCredentialNative+Credential]
        )
        if ($credential.CredentialBlobSize -eq 0) {
            return ''
        }
        return [Runtime.InteropServices.Marshal]::PtrToStringUni(
            $credential.CredentialBlob,
            [int]($credential.CredentialBlobSize / 2)
        )
    }
    finally {
        [APMA.WindowsCredentialNative]::CredFree($pointer)
    }
}

function Test-ApmaCredential {
    [CmdletBinding()]
    param([Parameter(Mandatory)][string]$Target)
    $value = Get-ApmaCredential -Target $Target
    $available = -not [string]::IsNullOrWhiteSpace($value)
    $value = $null
    return $available
}

function Remove-ApmaCredential {
    [CmdletBinding()]
    param([Parameter(Mandatory)][string]$Target)
    Assert-ApmaCredentialTarget -Target $Target
    if ([APMA.WindowsCredentialNative]::CredDelete(
        $Target,
        $script:ApmaCredentialTypeGeneric,
        0
    )) {
        return $true
    }
    $errorCode = [Runtime.InteropServices.Marshal]::GetLastWin32Error()
    if ($errorCode -eq 1168) {
        return $false
    }
    throw [ComponentModel.Win32Exception]::new($errorCode)
}

function Get-ApmaCredentialStatus {
    [CmdletBinding()]
    param(
        [string]$MeralionTarget = 'APMA/MERALION_API_KEY',
        [string]$GeminiTarget = 'APMA/GEMINI_API_KEY',
        [string]$DashScopeTarget = 'APMA/DASHSCOPE_API_KEY',
        [string]$OssAccessKeyIdTarget = 'APMA/ALIYUN_OSS_ACCESS_KEY_ID',
        [string]$OssAccessKeySecretTarget = 'APMA/ALIYUN_OSS_ACCESS_KEY_SECRET',
        [string]$OssEndpointTarget = 'APMA/ALIYUN_OSS_ENDPOINT',
        [string]$OssBucketTarget = 'APMA/ALIYUN_OSS_BUCKET'
    )
    $dashScopeConfigured = Test-ApmaCredential -Target $DashScopeTarget
    $ossAccessKeyIdConfigured = Test-ApmaCredential -Target $OssAccessKeyIdTarget
    $ossAccessKeySecretConfigured = Test-ApmaCredential -Target $OssAccessKeySecretTarget
    $ossEndpointConfigured = Test-ApmaCredential -Target $OssEndpointTarget
    $ossBucketConfigured = Test-ApmaCredential -Target $OssBucketTarget
    return [pscustomobject]@{
        MeralionConfigured = Test-ApmaCredential -Target $MeralionTarget
        GeminiConfigured = Test-ApmaCredential -Target $GeminiTarget
        DashScopeConfigured = $dashScopeConfigured
        OssAccessKeyIdConfigured = $ossAccessKeyIdConfigured
        OssAccessKeySecretConfigured = $ossAccessKeySecretConfigured
        OssEndpointConfigured = $ossEndpointConfigured
        OssBucketConfigured = $ossBucketConfigured
        QwenFiletransConfigured = (
            $dashScopeConfigured -and
            $ossAccessKeyIdConfigured -and
            $ossAccessKeySecretConfigured -and
            $ossEndpointConfigured -and
            $ossBucketConfigured
        )
    }
}
