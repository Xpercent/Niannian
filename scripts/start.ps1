param([int]$Port = 8765, [switch]$NoWeChat)
# The upstream WeChat adapter writes into a CWD-relative "wechatauto_logs" folder.
Set-Location -LiteralPath (Split-Path -Parent $PSScriptRoot)
$Arguments = @('-X', 'utf8', '-m', 'niannian', '--port', $Port)
if ($NoWeChat) { $Arguments += '--no-wechat' }
& .\.venv\Scripts\python.exe @Arguments
exit $LASTEXITCODE
