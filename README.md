# 地下城堡3 舆情监控

TapTap（评分 + 论坛 + 回复）与百度贴吧（地下城堡3吧）双数据源的舆情报告，自动推送到飞书群。

## 运行架构

```
外部服务器（10:00 / 周一 11:00）──API dispatch──┐
                                              ├─> GitHub Actions ─> 抓取 → 生成 HTML → Pages → 飞书
GitHub schedule（兜底，可能延迟）──────────────┘
```

- **主通道**：外部服务器通过 `trigger_github.py` 调 API 触发，保证准点。
- **兜底**：workflow 自带 `schedule`，机器不在线时仍会出报告（时间可能延后）。
- **防重复**：workflow 首个步骤检查「今日是否已有成功运行」，有则整体跳过；脚本内另有 `push_state.json` 幂等保护。两条通道同时触发也只会推一次。

## 报告页

- 日报：https://linyy663.github.io/dc3-monitor/daily/
- 周报：https://linyy663.github.io/dc3-monitor/weekly/

## 手动触发

```bash
# 正常推送（会受防重复守卫约束）
gh workflow run daily.yml --repo linyy663/dc3-monitor

# 只生成页面、不推送飞书
gh workflow run daily.yml --repo linyy663/dc3-monitor -f no_push=true

# 强制重跑（绕过防重复守卫）
gh workflow run daily.yml --repo linyy663/dc3-monitor -f force=true
```

周报把 `daily.yml` 换成 `weekly.yml` 即可。

## 单独服务器部署（准点触发）

把 `trigger_github.py`、`setup_scheduled_tasks.ps1`、`github_token.txt` 三个文件放到服务器同一目录，
以**管理员身份**运行一次：

```powershell
powershell -ExecutionPolicy Bypass -File .\setup_scheduled_tasks.ps1
```

会创建两个任务计划：

| 任务名 | 时间 | 动作 |
|---|---|---|
| DC3-每日舆情日报 | 每天 10:00 | `python trigger_github.py --daily` |
| DC3-每周舆情周报 | 每周一 11:00 | `python trigger_github.py --weekly` |

Token 读取优先级：环境变量 `GITHUB_TOKEN` → 同目录 `github_token.txt` → 脚本内 `FALLBACK_TOKEN`。
建议使用**无过期时间**的 Fine-grained PAT，权限勾选 `Contents: Read` + `Actions: Read and write`。

## 本地手动运行

```bash
pip install -r requirements.txt
python -m playwright install chromium

python dc3_report.py                 # 日报，抓昨天，生成并推送
python dc3_report.py --weekly        # 周报，抓上周一~周日
python dc3_report.py --no-push       # 只生成，不推送
python dc3_report.py --push-only --report-url <URL>   # 仅推送
python dc3_report.py --force         # 同日已推过仍强制重推
```

## 目录说明

```
dc3_report.py                 主脚本（日报/周报双模式）
requirements.txt              依赖
.github/workflows/daily.yml   日报工作流
.github/workflows/weekly.yml  周报工作流
trigger_github.py             外部服务器 API 触发器
setup_scheduled_tasks.ps1     外部服务器任务计划安装脚本
data/dc3/                     运行产物（不入库）
```
