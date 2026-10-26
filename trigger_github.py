"""
地下城堡3 舆情报告 —— GitHub Actions 工作流远程触发器

用途：部署在一台长期开机的服务器上，由 Windows 任务计划在固定时间调用，
     通过 GitHub API 触发 workflow，保证日报 10:00 / 周报周一 11:00 准点执行。
     （GitHub 自带的 schedule 对低活跃仓库会延迟数小时，不能用来保证准点）

用法:
  python trigger_github.py --daily                 # 触发日报（含飞书推送）
  python trigger_github.py --weekly                # 触发周报（含飞书推送）
  python trigger_github.py --daily --no-push       # 只生成页面，不推送飞书（测试 / 补页面）
  python trigger_github.py --daily --dry-run       # 只校验 Token 与配置，不真正触发

Token 获取优先级:
  1. 环境变量 GITHUB_TOKEN
  2. 脚本同目录下的 github_token.txt（文件内一行，就是 token 本身；已加入 .gitignore）
  3. 脚本内 FALLBACK_TOKEN 常量（不建议使用，仅应急）

Token 要求: 对 linyy663/dc3-monitor 具备 Actions: Read and write 权限。
          建议使用「无过期时间」的 Fine-grained PAT，避免再次出现 Token 到期导致推送中断。
"""

import os
import sys
import json
import urllib.request
import urllib.error
from datetime import datetime, timezone, timedelta

# ========== 配置 ==========
REPO_OWNER = "linyy663"
REPO_NAME = "dc3-monitor"
BRANCH = "master"

# 应急兜底 Token（留空即禁用；优先使用环境变量或 github_token.txt）
FALLBACK_TOKEN = ""

TOKEN_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "github_token.txt")

WORKFLOWS = {
    "daily": {
        "file": "daily.yml",
        "name": "地下城堡3 每日舆情日报",
    },
    "weekly": {
        "file": "weekly.yml",
        "name": "地下城堡3 每周舆情周报",
    },
}

API_BASE = f"https://api.github.com/repos/{REPO_OWNER}/{REPO_NAME}/actions/workflows"


def cst_now_str() -> str:
    tz = timezone(timedelta(hours=8))
    return datetime.now(tz).strftime("%Y-%m-%d %H:%M:%S")


def apply_headers(req: urllib.request.Request, token: str) -> None:
    req.add_header("Authorization", f"token {token}")
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("Content-Type", "application/json")
    req.add_header("User-Agent", "DC3-Trigger/1.0")


def load_token():
    """按优先级读取 Token: 环境变量 -> token 文件 -> 兜底常量"""
    token = (os.environ.get("GITHUB_TOKEN") or "").strip()
    if token:
        return token, "环境变量 GITHUB_TOKEN"

    if os.path.exists(TOKEN_FILE):
        try:
            with open(TOKEN_FILE, "r", encoding="utf-8") as f:
                token = f.read().strip()
            if token:
                return token, f"文件 {os.path.basename(TOKEN_FILE)}"
        except Exception as e:
            print(f"[警告] 读取 {TOKEN_FILE} 失败: {e}")

    if FALLBACK_TOKEN.strip():
        return FALLBACK_TOKEN.strip(), "脚本内 FALLBACK_TOKEN"

    return "", ""


def verify_token(token: str) -> bool:
    """调用 /user 校验 Token 是否仍然有效"""
    req = urllib.request.Request("https://api.github.com/user", method="GET")
    apply_headers(req, token)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="replace"))
            print(f"[Token] 有效，登录账号: {data.get('login')}")
            return True
    except urllib.error.HTTPError as e:
        if e.code == 401:
            print("[Token] 无效或已过期 (401 Bad credentials) —— 请更新 github_token.txt")
        else:
            print(f"[Token] 校验失败 HTTP {e.code}")
        return False
    except Exception as e:
        print(f"[Token] 校验异常: {e}")
        return False


def check_repo_access(token: str) -> bool:
    """确认 Token 对目标仓库有权限，尽早发现配置错误"""
    req = urllib.request.Request(f"https://api.github.com/repos/{REPO_OWNER}/{REPO_NAME}", method="GET")
    apply_headers(req, token)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            json.loads(resp.read().decode("utf-8", errors="replace"))
            print(f"[仓库] 可访问: {REPO_OWNER}/{REPO_NAME}")
            return True
    except urllib.error.HTTPError as e:
        print(f"[仓库] 无法访问 {REPO_OWNER}/{REPO_NAME} (HTTP {e.code}) —— Token 是否缺少该仓库权限？")
        return False
    except Exception as e:
        print(f"[仓库] 访问异常: {e}")
        return False


def dispatch_workflow(workflow_key: str, token: str, no_push: bool = False) -> bool:
    wf = WORKFLOWS[workflow_key]
    url = f"{API_BASE}/{wf['file']}/dispatches"

    payload_dict = {"ref": BRANCH}
    if no_push:
        payload_dict["inputs"] = {"no_push": "true"}

    payload = json.dumps(payload_dict).encode("utf-8")
    req = urllib.request.Request(url, data=payload, method="POST")
    apply_headers(req, token)

    flag = " [no-push 模式，不推送飞书]" if no_push else ""
    print(f"[{cst_now_str()}] 触发 {wf['name']} ({wf['file']}){flag} ...", end=" ")

    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            if resp.status == 204:
                print("成功 (204 No Content)")
                return True
            print(f"异常响应: {resp.status}")
            return False
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        hint = ""
        if e.code == 401:
            hint = "  <-- Token 失效，请更新 github_token.txt"
        elif e.code == 403:
            hint = "  <-- 无权限，请检查 PAT 是否勾选 Actions: Read and write"
        elif e.code == 404:
            hint = "  <-- 工作流不存在或无权限"
        elif e.code == 422:
            hint = "  <-- 分支名或 inputs 不被接受"
        print(f"失败 HTTP {e.code}: {body[:200]}{hint}")
        return False
    except Exception as e:
        print(f"失败: {e}")
        return False


def main():
    args = [a.lstrip("-").lower() for a in sys.argv[1:]]
    no_push = "no-push" in args or "no_push" in args
    dry_run = "dry-run" in args or "dry_run" in args
    keys = [a for a in args if a in WORKFLOWS]

    if not keys:
        print("用法: python trigger_github.py --daily | --weekly [--no-push] [--dry-run]")
        print()
        print("可用工作流:")
        for k, v in WORKFLOWS.items():
            print(f"  --{k:<8} {v['name']}")
        print()
        print("可选参数:")
        print("  --no-push      只生成页面，跳过飞书推送（测试 / 补页面）")
        print("  --dry-run      只校验 Token 与仓库权限，不真正触发")
        sys.exit(1)

    token, source = load_token()
    if not token:
        print("[错误] 未找到 Token。请任选一种方式配置：")
        print("  1) 设置环境变量 GITHUB_TOKEN")
        print(f"  2) 在同目录创建 {os.path.basename(TOKEN_FILE)}，内容为一行 Token")
        sys.exit(2)

    print(f"[Token] 来源: {source}")
    if not verify_token(token):
        sys.exit(2)
    if not check_repo_access(token):
        sys.exit(2)

    if dry_run:
        print("[dry-run] 校验通过，未触发任何工作流。")
        sys.exit(0)

    ok = True
    for k in keys:
        ok = dispatch_workflow(k, token, no_push=no_push) and ok

    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
