param([int]$Rounds = 1)
$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $ProjectRoot
$env:PYTHONUTF8 = '1'
for ($Round = 1; $Round -le $Rounds; $Round++) {
    Write-Host "验证轮次 $Round/$Rounds"
    & .\.venv\Scripts\python.exe -X utf8 -m unittest discover -s tests -v
    if ($LASTEXITCODE -ne 0) { throw "第 $Round 轮 Python 回归失败" }
    foreach ($Script in (Get-ChildItem shiguang/web -Filter '*.js')) {
        & node --check $Script.FullName
        if ($LASTEXITCODE -ne 0) { throw "前端语法失败：$($Script.Name)" }
    }
    if (Test-Path -LiteralPath 'tests/web.test.mjs') {
        & node tests/web.test.mjs
        if ($LASTEXITCODE -ne 0) { throw '前端纯函数回归失败' }
    }
}
Write-Host '验证通过。真实微信回放依赖 data/raw-wechat.json；缺失时对应测试明确跳过。'
