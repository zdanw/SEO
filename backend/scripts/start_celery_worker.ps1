# Windows 下启动 Celery Worker（需先激活 SEO conda env）
# 用法：在 backend 目录执行 .\scripts\start_celery_worker.ps1
#
# 队列拓扑（阶段 2）：
#   - 主 worker：default(tick) / serp(发现) / social(执行) / maintenance(回收对账)
#   - generation worker：AI 生成专用独立进程（长任务不占执行/tick 窗口）
Set-Location $PSScriptRoot\..

# generation 独立 worker：另起一个 PowerShell 窗口承载
$genCmd = "celery -A app.core.celery_app worker --loglevel=info --pool=solo -Q `"generation`""
Start-Process powershell -ArgumentList "-NoExit", "-Command", "Set-Location '$((Get-Location).Path)'; $genCmd"

celery -A app.core.celery_app worker `
  --loglevel=info `
  --pool=solo `
  -Q "default,serp,social,maintenance"
