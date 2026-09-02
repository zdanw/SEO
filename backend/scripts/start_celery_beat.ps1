# Windows 下启动 Celery Beat 定时调度
# 用法：在 backend 目录执行 .\scripts\start_celery_beat.ps1
Set-Location $PSScriptRoot\..

celery -A app.core.celery_app beat --loglevel=info
