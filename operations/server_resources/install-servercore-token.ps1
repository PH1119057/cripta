$ErrorActionPreference = 'Stop'

Write-Host 'Введите API-ключ Servercore. Символы отображаться не будут.'
$secure = Read-Host -AsSecureString 'API key'
$pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
try {
    $plain = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer)
    if ([string]::IsNullOrWhiteSpace($plain) -or $plain.Length -lt 16) {
        throw 'Ключ пустой или имеет подозрительно малую длину.'
    }
    $remote = @'
import os, pwd, grp, sys
token = sys.stdin.read().rstrip("\r\n")
if len(token) < 16 or "\n" in token or "\r" in token:
    raise SystemExit("invalid token")
path = "/etc/cripta/servercore.token"
fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o640)
try:
    os.write(fd, token.encode("utf-8"))
finally:
    os.close(fd)
os.chown(path, pwd.getpwnam("root").pw_uid, grp.getgrnam("cripta").gr_gid)
os.chmod(path, 0o640)
print("TOKEN_INSTALLED")
'@
    $encoded = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($remote))
    $plain | & ssh robot-admin "sudo python3 -c `"import base64;exec(base64.b64decode('$encoded'))`""
    if ($LASTEXITCODE -ne 0) { throw "Установка завершилась с кодом $LASTEXITCODE" }
}
finally {
    if ($pointer -ne [IntPtr]::Zero) {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer)
    }
    $plain = $null
    $secure = $null
}

Write-Host 'Ключ установлен. Его значение не выводилось и не передавалось в аргументах процесса.'
