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
& .\.venv\Scripts\python.exe -m pip install -e .
if ($LASTEXITCODE -ne 0) { throw '应用安装失败' }
& .\.venv\Scripts\python.exe -m pip install -e .\wechatauto-replica
if ($LASTEXITCODE -ne 0) { throw '微信读取依赖安装失败' }
if (Get-Command npm -ErrorAction SilentlyContinue) {
    Push-Location (Join-Path $ProjectRoot 'shiguang\web')
    try {
        npm ci
        if ($LASTEXITCODE -ne 0) { throw '前端依赖安装失败' }
        npm run build
        if ($LASTEXITCODE -ne 0) { throw '前端构建失败' }
    } finally { Pop-Location }
} elseif (-not (Test-Path -LiteralPath 'shiguang\web\dist\index.html')) {
    throw '缺少前端构建产物，请安装 Node.js 22.12+ 后重新运行 setup。'
}
Write-Host '安装完成。请确认微信 4.x 已登录，然后运行 scripts/start.ps1。'
