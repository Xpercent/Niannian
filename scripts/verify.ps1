param([int]$Rounds = 1)
$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $ProjectRoot
$env:PYTHONUTF8 = '1'
for ($Round = 1; $Round -le $Rounds; $Round++) {
    Write-Host "验证轮次 $Round/$Rounds"
    & .\.venv\Scripts\python.exe -X utf8 -m pytest tests -q
    if ($LASTEXITCODE -ne 0) { throw "第 $Round 轮 Python 回归失败" }
    Push-Location (Join-Path $ProjectRoot 'shiguang\web')
    try {
        npm run build
        if ($LASTEXITCODE -ne 0) { throw '前端类型检查或构建失败' }
    } finally { Pop-Location }
    if (Test-Path -LiteralPath 'tests/web.test.mjs') {
        & node --experimental-strip-types tests/web.test.mjs
        if ($LASTEXITCODE -ne 0) { throw '前端纯函数回归失败' }
    }
}
Write-Host '验证通过。真实微信回放依赖 data/raw-wechat.json；缺失时对应测试明确跳过。'
