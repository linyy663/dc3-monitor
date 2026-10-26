# ============================================================
# 地下城堡3 舆情监控 - Windows 定时任务安装脚本
#
# 用途：在 Windows 任务计划程序中创建定时任务，准点触发 GitHub Actions
#       （GitHub 自带的 schedule 对低活跃仓库会延迟数小时，不能用来保证准点）
#
# 部署文件（3 个，放在同一个目录）：
#   trigger_github.py          —— 触发器脚本
#   github_token.txt           —— 一行，就是 GitHub Token（Actions: Read and write）
#   setup_scheduled_tasks.ps1  —— 本脚本
#
# 要求：以管理员身份运行 PowerShell
# 用法：右键此文件 → "使用 PowerShell 运行"
#       或管理员 PowerShell 中执行: .\setup_scheduled_tasks.ps1
# ============================================================

$ErrorActionPreference = "Stop"

# 自动检测脚本所在目录
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$PythonExe = (Get-Command python -ErrorAction SilentlyContinue).Source
if (-not $PythonExe) {
    $PythonExe = (Get-Command python3 -ErrorAction SilentlyContinue).Source
}
if (-not $PythonExe) {
    Write-Host "[错误] 未找到 Python，请先安装 Python 并添加到 PATH" -ForegroundColor Red
    exit 1
}

$TriggerScript = Join-Path $ScriptDir "trigger_github.py"
$TokenFile = Join-Path $ScriptDir "github_token.txt"

if (-not (Test-Path $TriggerScript)) {
    Write-Host "[错误] 未找到 $TriggerScript" -ForegroundColor Red
    exit 1
}

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  地下城堡3 舆情监控 - 定时任务安装" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "脚本路径: $TriggerScript"
Write-Host "Python:    $PythonExe"

# ---------- Token 预检：没有有效 Token 就别装任务 ----------
Write-Host ""
Write-Host "[预检] 校验 GitHub Token 与仓库权限 ..." -ForegroundColor Yellow
if (-not (Test-Path $TokenFile)) {
    Write-Host "[错误] 未找到 $TokenFile" -ForegroundColor Red
    Write-Host "       请在该文件内写入一行 GitHub Token（需 Actions: Read and write 权限）" -ForegroundColor Yellow
    Write-Host "       建议使用无过期时间的 Fine-grained PAT，避免因过期而静默中断" -ForegroundColor Yellow
    exit 1
}

& $PythonExe $TriggerScript --daily --dry-run
if ($LASTEXITCODE -ne 0) {
    Write-Host "[错误] Token 或仓库权限校验未通过，已中止安装（请更新 github_token.txt 后重试）" -ForegroundColor Red
    exit 1
}
Write-Host "[OK] Token 与仓库权限均正常" -ForegroundColor Green
Write-Host ""

# ---------- 日报任务（每天 10:00）----------
$DailyTaskName = "DC3-DailyReport"
$DailyAction = New-ScheduledTaskAction -Execute $PythonExe `
    -Argument "`"$TriggerScript`" --daily" `
    -WorkingDirectory $ScriptDir

$DailyTrigger = New-ScheduledTaskTrigger -Daily -At "10:00"
$DailySettings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -RunOnlyIfNetworkAvailable `
    -MultipleInstances IgnoreNew

$DailyPrincipal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount

try {
    Unregister-ScheduledTask -TaskName $DailyTaskName -ErrorAction SilentlyContinue -Confirm:$false

    Register-ScheduledTask -TaskName $DailyTaskName `
        -Action $DailyAction `
        -Trigger $DailyTrigger `
        -Settings $DailySettings `
        -Principal $DailyPrincipal `
        -Description "地下城堡3 每日舆情日报触发器 — 每天 10:00 调用 GitHub Actions"

    Write-Host "[OK] 日报任务已创建: 每天 10:00" -ForegroundColor Green
} catch {
    Write-Host "[失败] 日报任务创建失败: $_" -ForegroundColor Red
}

# ---------- 周报任务（每周一 11:00）----------
$WeeklyTaskName = "DC3-WeeklyReport"
$WeeklyAction = New-ScheduledTaskAction -Execute $PythonExe `
    -Argument "`"$TriggerScript`" --weekly" `
    -WorkingDirectory $ScriptDir

$WeeklyTrigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday -At "11:00"
$WeeklySettings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -RunOnlyIfNetworkAvailable `
    -MultipleInstances IgnoreNew

$WeeklyPrincipal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount

try {
    Unregister-ScheduledTask -TaskName $WeeklyTaskName -ErrorAction SilentlyContinue -Confirm:$false

    Register-ScheduledTask -TaskName $WeeklyTaskName `
        -Action $WeeklyAction `
        -Trigger $WeeklyTrigger `
        -Settings $WeeklySettings `
        -Principal $WeeklyPrincipal `
        -Description "地下城堡3 每周舆情周报触发器 — 每周一 11:00 调用 GitHub Actions"

    Write-Host "[OK] 周报任务已创建: 每周一 11:00" -ForegroundColor Green
} catch {
    Write-Host "[失败] 周报任务创建失败: $_" -ForegroundColor Red
}

Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "安装完成！验证命令：" -ForegroundColor Yellow
Write-Host "  Get-ScheduledTask -TaskName 'DC3-*' | Select TaskName, State" -ForegroundColor White
Write-Host ""
Write-Host "手动测试执行（不会重复推送，workflow 内置防重复守卫）：" -ForegroundColor Yellow
Write-Host "  Start-ScheduledTask -TaskName 'DC3-DailyReport'" -ForegroundColor White
Write-Host ""
Write-Host "单跑校验 Token（不触发任何工作流）：" -ForegroundColor Yellow
Write-Host "  python trigger_github.py --daily --dry-run" -ForegroundColor White
Write-Host "========================================" -ForegroundColor Cyan
