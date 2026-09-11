Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

. (Join-Path $PSScriptRoot 'APMA_CredentialStore.ps1')

Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing

$meralionTarget = 'APMA/MERALION_API_KEY'
$geminiTarget = 'APMA/GEMINI_API_KEY'
$dashScopeTarget = 'APMA/DASHSCOPE_API_KEY'
$ossAccessKeyIdTarget = 'APMA/ALIYUN_OSS_ACCESS_KEY_ID'
$ossAccessKeySecretTarget = 'APMA/ALIYUN_OSS_ACCESS_KEY_SECRET'
$ossEndpointTarget = 'APMA/ALIYUN_OSS_ENDPOINT'
$ossBucketTarget = 'APMA/ALIYUN_OSS_BUCKET'

$form = New-Object Windows.Forms.Form
$form.Text = 'APMA Secure Provider Credentials'
$form.StartPosition = 'CenterScreen'
$form.Size = New-Object Drawing.Size(720, 750)
$form.MinimumSize = $form.Size
$form.MaximizeBox = $false
$form.FormBorderStyle = 'FixedDialog'

$title = New-Object Windows.Forms.Label
$title.Text = 'Configure APMA provider keys once'
$title.Font = New-Object Drawing.Font('Segoe UI', 15, [Drawing.FontStyle]::Bold)
$title.Location = New-Object Drawing.Point(24, 20)
$title.AutoSize = $true
$form.Controls.Add($title)

$note = New-Object Windows.Forms.Label
$note.Text = "Keys are stored for this Windows user in Windows Credential Manager; complete values are never displayed.`nShort bounded Qwen clips use the DashScope key only. Private OSS is optional for larger production inputs."
$note.Location = New-Object Drawing.Point(27, 58)
$note.Size = New-Object Drawing.Size(660, 42)
$form.Controls.Add($note)

function Add-CredentialRow {
    param([string]$Label, [int]$Top, [bool]$Secret = $true, [string]$DefaultText = '')
    $name = New-Object Windows.Forms.Label
    $name.Text = $Label
    $name.Location = New-Object Drawing.Point(28, $Top)
    $name.Size = New-Object Drawing.Size(430, 22)
    $form.Controls.Add($name)

    $status = New-Object Windows.Forms.Label
    $status.Location = New-Object Drawing.Point(485, $Top)
    $status.Size = New-Object Drawing.Size(190, 22)
    $status.Font = New-Object Drawing.Font('Segoe UI', 9, [Drawing.FontStyle]::Bold)
    $form.Controls.Add($status)

    $box = New-Object Windows.Forms.TextBox
    $box.Location = New-Object Drawing.Point(28, ($Top + 26))
    $box.Size = New-Object Drawing.Size(647, 24)
    $box.UseSystemPasswordChar = $Secret
    $box.Text = $DefaultText
    $form.Controls.Add($box)
    return [pscustomobject]@{ Status = $status; Box = $box }
}

$meralionRow = Add-CredentialRow -Label 'MERaLiON hosted transcription API key' -Top 104
$geminiRow = Add-CredentialRow -Label 'Configure Gemini key' -Top 168
$dashScopeRow = Add-CredentialRow -Label 'Alibaba Model Studio API key (region must match the configured endpoint)' -Top 232
$ossAccessKeyIdRow = Add-CredentialRow -Label 'Optional private OSS AccessKey ID (use a restricted RAM user)' -Top 296
$ossAccessKeySecretRow = Add-CredentialRow -Label 'Optional private OSS AccessKey Secret' -Top 360
$ossEndpointRow = Add-CredentialRow -Label 'Optional Singapore OSS endpoint' -Top 424 -Secret $false -DefaultText 'https://oss-ap-southeast-1.aliyuncs.com'
$ossBucketRow = Add-CredentialRow -Label 'Optional private Singapore OSS bucket name' -Top 488 -Secret $false

function Update-CredentialStatus {
    $status = Get-ApmaCredentialStatus
    $meralionRow.Status.Text = if ($status.MeralionConfigured) { 'Configured: YES' } else { 'Configured: NO' }
    $geminiRow.Status.Text = if ($status.GeminiConfigured) { 'Configured: YES' } else { 'Configured: NO' }
    $dashScopeRow.Status.Text = if ($status.DashScopeConfigured) { 'Configured: YES' } else { 'Configured: NO' }
    $ossAccessKeyIdRow.Status.Text = if ($status.OssAccessKeyIdConfigured) { 'Configured: YES' } else { 'Configured: NO' }
    $ossAccessKeySecretRow.Status.Text = if ($status.OssAccessKeySecretConfigured) { 'Configured: YES' } else { 'Configured: NO' }
    $ossEndpointRow.Status.Text = if ($status.OssEndpointConfigured) { 'Configured: YES' } else { 'Configured: NO' }
    $ossBucketRow.Status.Text = if ($status.OssBucketConfigured) { 'Configured: YES' } else { 'Configured: NO' }
    $meralionRow.Status.ForeColor = if ($status.MeralionConfigured) { [Drawing.Color]::DarkGreen } else { [Drawing.Color]::DarkRed }
    $geminiRow.Status.ForeColor = if ($status.GeminiConfigured) { [Drawing.Color]::DarkGreen } else { [Drawing.Color]::DarkRed }
    foreach ($pair in @(
        @($dashScopeRow, $status.DashScopeConfigured),
        @($ossAccessKeyIdRow, $status.OssAccessKeyIdConfigured),
        @($ossAccessKeySecretRow, $status.OssAccessKeySecretConfigured),
        @($ossEndpointRow, $status.OssEndpointConfigured),
        @($ossBucketRow, $status.OssBucketConfigured)
    )) {
        $pair[0].Status.ForeColor = if ($pair[1]) { [Drawing.Color]::DarkGreen } else { [Drawing.Color]::DarkRed }
    }
}

$save = New-Object Windows.Forms.Button
$save.Text = 'Save securely'
$save.Location = New-Object Drawing.Point(28, 565)
$save.Size = New-Object Drawing.Size(130, 34)
$save.Add_Click({
    try {
        $saved = $false
        if (-not [string]::IsNullOrWhiteSpace($meralionRow.Box.Text)) {
            $null = Set-ApmaCredential -Target $meralionTarget -Secret $meralionRow.Box.Text
            [Environment]::SetEnvironmentVariable('MERALION_API_KEY', $null, 'User')
            $meralionRow.Box.Clear()
            $saved = $true
        }
        if (-not [string]::IsNullOrWhiteSpace($geminiRow.Box.Text)) {
            $null = Set-ApmaCredential -Target $geminiTarget -Secret $geminiRow.Box.Text
            [Environment]::SetEnvironmentVariable('GEMINI_API_KEY', $null, 'User')
            $geminiRow.Box.Clear()
            $saved = $true
        }
        foreach ($entry in @(
            @($dashScopeRow, $dashScopeTarget, 'DASHSCOPE_API_KEY'),
            @($ossAccessKeyIdRow, $ossAccessKeyIdTarget, 'ALIYUN_OSS_ACCESS_KEY_ID'),
            @($ossAccessKeySecretRow, $ossAccessKeySecretTarget, 'ALIYUN_OSS_ACCESS_KEY_SECRET'),
            @($ossEndpointRow, $ossEndpointTarget, 'ALIYUN_OSS_ENDPOINT'),
            @($ossBucketRow, $ossBucketTarget, 'ALIYUN_OSS_BUCKET')
        )) {
            if (-not [string]::IsNullOrWhiteSpace($entry[0].Box.Text)) {
                $null = Set-ApmaCredential -Target $entry[1] -Secret $entry[0].Box.Text.Trim()
                [Environment]::SetEnvironmentVariable($entry[2], $null, 'User')
                $entry[0].Box.Clear()
                $saved = $true
            }
        }
        $ossEndpointRow.Box.Text = 'https://oss-ap-southeast-1.aliyuncs.com'
        Update-CredentialStatus
        if ($saved) {
            [Windows.Forms.MessageBox]::Show(
                'Saved securely. Any matching plaintext Windows user variable was removed.',
                'APMA',
                'OK',
                'Information'
            ) | Out-Null
        }
    }
    catch {
        [Windows.Forms.MessageBox]::Show($_.Exception.Message, 'APMA', 'OK', 'Error') | Out-Null
    }
})
$form.Controls.Add($save)

function Add-ForgetButton {
    param([string]$Text, [int]$Left, [string[]]$Targets)
    $button = New-Object Windows.Forms.Button
    $button.Text = $Text
    $button.Location = New-Object Drawing.Point($Left, 565)
    $button.Size = New-Object Drawing.Size(155, 34)
    $button.Add_Click({
        $answer = [Windows.Forms.MessageBox]::Show(
            "Remove $Text?",
            'APMA',
            'YesNo',
            'Question'
        )
        if ($answer -eq [Windows.Forms.DialogResult]::Yes) {
            foreach ($target in $Targets) {
                $null = Remove-ApmaCredential -Target $target
            }
            Update-CredentialStatus
        }
    }.GetNewClosure())
    $form.Controls.Add($button)
}

Add-ForgetButton -Text 'Forget MERaLiON' -Left 175 -Targets @($meralionTarget)
Add-ForgetButton -Text 'Forget Gemini' -Left 347 -Targets @($geminiTarget)
Add-ForgetButton -Text 'Forget Qwen + OSS' -Left 519 -Targets @(
    $dashScopeTarget,
    $ossAccessKeyIdTarget,
    $ossAccessKeySecretTarget,
    $ossEndpointTarget,
    $ossBucketTarget
)

$close = New-Object Windows.Forms.Button
$close.Text = 'Close'
$close.Location = New-Object Drawing.Point(547, 625)
$close.Size = New-Object Drawing.Size(130, 30)
$close.Add_Click({ $form.Close() })
$form.Controls.Add($close)

Update-CredentialStatus
$form.Add_Shown({ $form.Activate(); $meralionRow.Box.Focus() })
[void]$form.ShowDialog()
