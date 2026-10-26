"""抓取结果校验：数据为空则让工作流失败（避免反爬拦截后静默出空报告）。

用法: python scripts/check_data.py daily|weekly
"""
import glob
import json
import os
import sys


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "daily"
    pattern = f"data/dc3/dc3_{'week' if mode == 'weekly' else 'daily'}_*.json"

    files = sorted(glob.glob(pattern), key=os.path.getmtime)
    if not files:
        print("::error::未生成任何数据文件，抓取失败")
        return 1

    with open(files[-1], encoding="utf-8") as f:
        items = json.load(f)

    counts = {}
    for it in items:
        src = it.get("source", "?")
        counts[src] = counts.get(src, 0) + 1

    print(f"数据文件: {files[-1]}")
    print(f"总条数: {len(items)}")
    print(f"来源分布: {counts}")

    if len(items) == 0:
        print("::error::抓取结果为空（可能被反爬拦截），终止本次运行")
        return 1

    if not any(k.startswith("tieba") for k in counts):
        print("::warning::贴吧数据为 0，可能被拦截，本次仅 TapTap 数据可用")

    return 0


if __name__ == "__main__":
    sys.exit(main())
