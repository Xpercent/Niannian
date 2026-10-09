param([int]$Port = 8765, [switch]$NoWeChat)
$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$PythonPath = Join-Path $ProjectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $PythonPath)) {
    throw '请先执行 scripts/setup.ps1 安装依赖。'
}
Set-Location -LiteralPath $ProjectRoot
$LaunchArgs = @('-X', 'utf8', '-m', 'shiguang', '--port', "$Port")
if ($NoWeChat) { $LaunchArgs += '--no-wechat' }
Write-Host "拾光工作台 http://127.0.0.1:$Port"
& $PythonPath @LaunchArgs
exit $LASTEXITCODE
