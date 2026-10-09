param(
    [Parameter(Mandatory=$true)][ValidateSet('generate','public','sign')][string]$Mode,
    [Parameter(Mandatory=$true)][string]$KeyPath,
    [string]$PayloadBase64
)
$ErrorActionPreference='Stop'
[Console]::OutputEncoding=[Text.UTF8Encoding]::new($false)
$rsa=[Security.Cryptography.RSA]::Create()
try {
    if ($Mode -eq 'generate') {
        if ([IO.File]::Exists($KeyPath)) { throw 'Signing key already exists; never overwrite.' }
        $rsa.KeySize=2048
        $xml=$rsa.ToXmlString($true)
        $bytes=[Text.UTF8Encoding]::new($false).GetBytes($xml)
        $stream=[IO.File]::Open($KeyPath,[IO.FileMode]::CreateNew,[IO.FileAccess]::Write,[IO.FileShare]::None)
        try { $stream.Write($bytes,0,$bytes.Length); $stream.Flush() } finally { $stream.Dispose() }
    } else {
        $rsa.FromXmlString([IO.File]::ReadAllText($KeyPath,[Text.Encoding]::UTF8))
        if ($rsa.KeySize -ne 2048) { throw 'Unexpected signing key size.' }
    }
    $public=$rsa.ExportParameters($false)
    $result=@{
        format='pinganxile-public-key-v1'
        modulus=[Convert]::ToBase64String($public.Modulus)
        exponent=[Convert]::ToBase64String($public.Exponent)
    }
    if ($Mode -eq 'sign') {
        $payload=[Convert]::FromBase64String($PayloadBase64)
        if ($payload.Length -ne 57) { throw 'Unexpected license payload length.' }
        $signature=$rsa.SignData($payload,[Security.Cryptography.HashAlgorithmName]::SHA256,
                               [Security.Cryptography.RSASignaturePadding]::Pkcs1)
        $result.signature=[Convert]::ToBase64String($signature)
    }
    $result | ConvertTo-Json -Compress
} finally { $rsa.Dispose() }

