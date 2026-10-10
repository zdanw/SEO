# Windows 下启动 Celery Worker（需先激活 SEO conda 环境）
# 用法：在 backend 目录执行 .\scripts\start_celery_worker.ps1
Set-Location $PSScriptRoot\..

celery -A app.core.celery_app worker `
  --loglevel=info `
  --pool=solo `
  -Q "default,serp,social"
