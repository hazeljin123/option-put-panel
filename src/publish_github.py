#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把生成好的面板同步到 GitHub 仓库并推送，触发 GitHub Pages 重建。

仓库结构（与 hazeljin123/oilseed-futures-panel 一致的惯例）：
  仓库根/            <- Pages 发布源（legacy 模式 source=main, path=/）
    index.html         统一入口（CBOT 理论带 + 国内真实持仓集中度）
    cn_panel.html      国内商品期权独立面板
    real_band.html     CBOT 三列理论带
    data/              每日 JSON / Markdown 产物（历史留档）
    archive/           每日 HTML 快照
    .nojekyll          禁用 Jekyll（否则 .md 归档会被 Jekyll 处理导致构建失败）
  src/               <- 本项目源码

为什么用「Pages 内置发布器」而不是自建 Actions 工作流：
  本机 GitHub 令牌的 OAuth scope 只有 repo，没有 workflow，
  无法创建/修改 .github/workflows/*.yml（GitHub 会直接拒绝）。
  但 legacy Pages 在 main 分支内容变化时会自动重建 —— 这正好够用，
  且不依赖任何自定义工作流。

安全：
  - 令牌从 git credential manager 读取，不写进任何文件
  - 推完后立刻把 remote 还原为无令牌的地址，避免令牌残留在 .git/config
  - 推送前做结构校验，不合格则拒绝推送（防止线上出现空白页/空壳）
"""
import datetime
import json
import os
import re
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))          # .../src
ROOT = os.path.dirname(HERE)                               # 仓库根
OUT = os.path.join(HERE, "output")
REPO = "hazeljin123/option-put-panel"
BRANCH = "main"
REMOTE = f"https://github.com/{REPO}.git"

# 每天发布的产物（相对仓库根）
PUSH_FILES = ["index.html", "cn_panel.html", "real_band.html"]
PUSH_DIRS = ["data", "archive"]


def log(msg):
    print(f"[publish] {msg}", file=sys.stderr)


def sh(cmd, cwd=ROOT, timeout=180):
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=timeout)


def get_token():
    """从 git credential manager 取 github.com 的令牌。

    注意：Git Credential Manager 在**无 TTY** 环境（被 subprocess 调起时）
    会尝试弹窗/浏览器交互而挂起，必须设 GIT_TERMINAL_PROMPT=0 让它
    直接返回已存储的凭据，否则会一直等到超时。
    """
    env = dict(os.environ)
    # 只设 GIT_TERMINAL_PROMPT=0：禁止 git 弹终端提示，但仍允许 GCM 读取已存凭据。
    # 千万不要设 GCM_INTERACTIVE=never —— 实测那会连已存凭据也读不到，
    # 直接报 "Cannot prompt because user interactivity has been disabled"。
    env["GIT_TERMINAL_PROMPT"] = "0"
    for attempt in range(3):
        try:
            r = subprocess.run(["git", "credential", "fill"],
                               input="protocol=https\nhost=github.com\n\n",
                               capture_output=True, text=True, encoding="utf-8",
                               errors="replace", timeout=20, env=env)
            for line in (r.stdout or "").splitlines():
                if line.startswith("password="):
                    tok = line.split("=", 1)[1].strip()
                    if tok:
                        return tok
        except subprocess.TimeoutExpired:
            log(f"读取凭据超时（第 {attempt + 1} 次），重试…")
        except Exception as e:  # noqa: BLE001
            log(f"读取凭据失败: {e}")
            break
    return None


# ------------------------------------------------------------------ 校验
def validate():
    """推送前的机器校验。任一项不过 -> 拒绝推送（防止线上空壳/空白页）。"""
    problems = []

    idx = os.path.join(OUT, "index.html")
    if not os.path.exists(idx):
        problems.append("缺少 output/index.html")
    elif os.path.getsize(idx) < 4000:
        problems.append(f"index.html 过小（{os.path.getsize(idx)} 字节），疑似空壳")
    else:
        html = open(idx, encoding="utf-8", errors="replace").read()
        for ph in set(re.findall(r"__[A-Z_]{2,}__", html)):
            problems.append(f"index.html 含未替换占位符 {ph}")
        for key in ("国内商品期权", "卖Put", "卖Call", "CBOT"):
            if key not in html:
                problems.append(f"index.html 缺少关键内容「{key}」")

    # 国内面板 JSON：8 品种、delta 非空、OI>0、expiry 格式正确
    jf = None
    if os.path.isdir(OUT):
        for fn in sorted(os.listdir(OUT)):
            if fn.startswith("cn_panel_") and fn.endswith(".json"):
                jf = os.path.join(OUT, fn)
    if not jf:
        problems.append("缺少 cn_panel_*.json")
    else:
        try:
            d = json.load(open(jf, encoding="utf-8"))
        except Exception as e:  # noqa: BLE001
            problems.append(f"cn_panel JSON 解析失败: {e}")
            d = None
        if d:
            rows = d.get("rows") or []
            if len(rows) < 8:
                problems.append(f"国内品种数 {len(rows)} < 8")
            for r in rows:
                for side in ("put_top", "call_top"):
                    rec = r.get(side)
                    if not rec:
                        problems.append(f"{r.get('name')} {side} 缺失")
                        continue
                    if rec.get("delta") is None:
                        problems.append(f"{r.get('name')} {side} delta 为空")
                    if not (rec.get("oi") or 0) > 0:
                        problems.append(f"{r.get('name')} {side} OI 为 0")
                if not re.match(r"^\d{4}-\d{2}-\d{2}$", str(r.get("expiry") or "")):
                    problems.append(f"{r.get('name')} expiry 格式异常: {r.get('expiry')}")
            cdd = d.get("czce_data_date")
            if cdd:
                log(f"国内面板数据日期：报告日 {d.get('date')}，郑商所官方实际 {cdd}")

    return problems, jf


# ------------------------------------------------------------------ 布置产物
def stage_files():
    """把产物拷到仓库根（Pages 从 main 分支根目录发布）。"""
    for d in PUSH_DIRS:
        p = os.path.join(ROOT, d)
        os.makedirs(p, exist_ok=True)
        for fn in os.listdir(p):
            fp = os.path.join(p, fn)
            if os.path.isfile(fp):
                try:
                    os.remove(fp)
                except OSError:
                    pass

    for fn in PUSH_FILES:
        src = os.path.join(OUT, fn)
        if os.path.exists(src):
            shutil.copy2(src, os.path.join(ROOT, fn))

    data_dir = os.path.join(ROOT, "data")
    arch_dir = os.path.join(ROOT, "archive")
    os.makedirs(data_dir, exist_ok=True)
    os.makedirs(arch_dir, exist_ok=True)
    for fn in sorted(os.listdir(OUT)):
        src = os.path.join(OUT, fn)
        if fn.endswith(".json") or fn.endswith(".md"):
            shutil.copy2(src, os.path.join(data_dir, fn))
    for fn in ("index.html", "cn_panel.html"):
        src = os.path.join(OUT, fn)
        if os.path.exists(src):
            shutil.copy2(src, os.path.join(arch_dir, fn))

    # .nojekyll：让 Pages 原样发布静态文件，不走 Jekyll
    # （Jekyll 会把 data/*.md 当页面处理并可能因 Liquid 语法报错导致构建失败）
    with open(os.path.join(ROOT, ".nojekyll"), "w", encoding="utf-8") as f:
        f.write("")


def commit_and_push(token):
    url = f"https://x-access-token:{token}@github.com/{REPO}.git"
    sh(["git", "remote", "set-url", "origin", url])

    try:
        # 逐个添加，避免 pathspec 无匹配时 git add 整体失败
        sh(["git", "add", "--", ".nojekyll"])
        for fn in PUSH_FILES:
            sh(["git", "add", "--", fn])
        for d in PUSH_DIRS:
            sh(["git", "add", "--", d])

        r = sh(["git", "diff", "--cached", "--quiet"])
        if r.returncode == 0:
            log("产物无变化，跳过推送（线上仍是上次内容）")
            return True

        stamp = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8)))
        msg = f"daily update {stamp.strftime('%Y-%m-%d %H:%M')} 北京时间"
        r = sh(["git", "commit", "-m", msg])
        if r.returncode != 0:
            log(f"commit 失败: {r.stderr[-500:]}")
            return False

        r = sh(["git", "push", "origin", BRANCH], timeout=300)
        if r.returncode != 0:
            log(f"push 失败: {r.stderr[-800:]}")
            return False
        log(f"已推送: {msg}")
        return True
    finally:
        # 还原 remote，避免令牌残留在 .git/config
        sh(["git", "remote", "set-url", "origin", REMOTE])


def main():
    if not os.path.isdir(OUT):
        log(f"❌ 找不到产物目录 {OUT}，请先运行 daily.py")
        return 1

    problems, jf = validate()
    if problems:
        log("❌ 校验未通过，拒绝推送：")
        for p in problems:
            log(f"   - {p}")
        return 1

    log(f"校验通过（{os.path.basename(jf) if jf else '—'}）")
    stage_files()

    token = get_token()
    if not token:
        log("❌ 未取到 GitHub 凭据（git credential manager）")
        return 2

    if commit_and_push(token):
        log(f"✅ 已推送到 {REPO}，Pages 将自动重建")
        return 0
    return 3


if __name__ == "__main__":
    sys.exit(main())
