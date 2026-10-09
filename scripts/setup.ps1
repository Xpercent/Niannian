$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $ProjectRoot
if (-not (Test-Path -LiteralPath '.venv\Scripts\python.exe')) {
    python -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw '创建 Python 虚拟环境失败' }
}
git submodule update --init
if ($LASTEXITCODE -ne 0) { throw '微信读取依赖初始化失败' }
& .\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
if ($LASTEXITCODE -ne 0) { throw '应用依赖安装失败' }
& .\.venv\Scripts\python.exe -m pip install -e .\wechatauto-replica
if ($LASTEXITCODE -ne 0) { throw '微信读取依赖安装失败' }
Write-Host '安装完成。请确认微信 4.x 已登录，然后运行 scripts/start.ps1。'
