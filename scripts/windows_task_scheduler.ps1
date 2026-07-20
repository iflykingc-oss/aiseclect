# Windows 任务计划程序 - 注册 aiseclect 定时任务
# 用法（在 PowerShell 管理员模式下）：
#   1) cd <项目根目录>
#   2) .\scripts\windows_task_scheduler.ps1 -Install
#   3) .\scripts\windows_task_scheduler.ps1 -Uninstall  （卸载时）

param(
    [switch]$Install,
    [switch]$Uninstall,
    [string]$TaskName = "aiseclect-flow",
    [int]$IntervalHours = 4
)

$ErrorActionPreference = "Stop"
$ProjectRoot = (Resolve-Path "$PSScriptRoot\..").Path
$PythonExe = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$ScriptPath = Join-Path $ProjectRoot "scripts\run_loop.py"

if ($Uninstall) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Host "已卸载任务: $TaskName" -ForegroundColor Green
    exit 0
}

if (-not (Test-Path $PythonExe)) {
    Write-Host "未找到 venv: $PythonExe，先执行 uv venv && uv sync" -ForegroundColor Red
    exit 1
}

# 任务动作：循环运行
$Action = New-ScheduledTaskAction `
    -Execute $PythonExe `
    -Argument "-u $ScriptPath --interval-hours $IntervalHours" `
    -WorkingDirectory $ProjectRoot

# 触发器：开机后 1 分钟开始首跑，之后每 N 小时循环（无限期）
$Trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) `
    -RepetitionInterval (New-TimeSpan -Hours $IntervalHours) `
    -RepetitionDuration (New-TimeSpan -Days 3650)
$Settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $Action `
    -Trigger $Trigger `
    -Settings $Settings `
    -Description "AI 资讯采集 → 飞书推文工作流，每 $IntervalHours 小时一次" `
    -RunLevel Highest `
    -Force | Out-Null

Write-Host "已注册任务: $TaskName (每 $IntervalHours 小时一次)" -ForegroundColor Green
Write-Host "查看: Get-ScheduledTask -TaskName $TaskName" -ForegroundColor Cyan
Write-Host "立即跑: Start-ScheduledTask -TaskName $TaskName" -ForegroundColor Cyan
Write-Host "卸载: $PSCommandPath -Uninstall" -ForegroundColor Cyan
