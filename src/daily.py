#!/usr/bin/env python3
# 每日编排（优化版，对齐豆包「免费公开逐档 OI」路线）：
#   ① 始终跑 real_band.py  -> output/real_band.html（真实新浪期货价 + Black-76 理论卖Put带/卖Call下沿，3列）
#   ② 始终跑 cn_panel.py  -> output/cn_panel.html + cn_panel_<date>.json
#       国内8品种真实逐档持仓量（郑商所官方每日行情表 + 东方财富公开行情），
#       交易所口径 IV/Delta，可直接定位"卖Put/卖Call 持仓最集中行权价"
#   ③ best-effort 抓 Barchart 免费逐档总 OI -> 成功则跑 pipeline.run() -> output/dashboard.html
#       （CBOT 真实 OI 集中度 5 步评分；沙箱被反爬/代理挡时优雅失败，不假装更新）
#   ④ 汇总 build_index.py -> output/index.html（CBOT + 国内 统一入口）
import sys, os, json, datetime, subprocess

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)
TODAY = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8))).strftime("%Y-%m-%d")


def run_script(name):
    r = subprocess.run([sys.executable, os.path.join(BASE, name)], cwd=BASE,
                       capture_output=True, text=True, timeout=900)
    print(f"--- {name} ---\n{r.stdout}\n{r.stderr}")
    return r


def set_demo(off):
    cfg_path = os.path.join(BASE, "config.json")
    cfg = json.load(open(cfg_path, encoding="utf-8"))
    cfg["demo"] = (not off)
    json.dump(cfg, open(cfg_path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)


def main():
    print(f"=== 报告日 {TODAY} ===")

    # ① CBOT 理论带（真实新浪价）—— 始终产出
    run_script("real_band.py")

    # ② 国内商品期权真实持仓集中度 —— 始终尝试（交易所公开源，稳定可达）
    run_script("cn_panel.py")

    # ③ CBOT 真实逐档 OI：优先 Barchart 免费源
    print("=== 抓取免费逐档 OI (Barchart Essentials) ===")
    real_oi_csv = None
    try:
        import fetch_barchart
        real_oi_csv = fetch_barchart.fetch(TODAY)
    except Exception as e:
        print("fetch_barchart 异常：", e)

    if real_oi_csv:
        set_demo(True)  # 有真实 OI -> 关闭演示横幅
        run_script("pipeline.py")
        print(f"✅ 真实逐档 OI 面板已更新：output/dashboard.html（数据 {TODAY}）")
    else:
        print("⚠️ 未获取到当日真实逐档 OI（Barchart 被反爬/代理挡，或本环境无网络）。")
        print("   -> dashboard.html 需在你本机（正常出网）运行 daily.py，或把 QuikStrike/Barchart 导出 CSV 放入 data/incoming/。")
        print("   -> CBOT 区块仍提供 real_band.html（真实期货价理论带）。")

    # ④ 统一入口页
    run_script("build_index.py")

    # ⑤ 推送到 GitHub（触发 Pages 重建）
    # 令牌 scope 不足时优雅失败：面板仍可从 WorkBuddy 托管地址访问
    try:
        import publish_github
        rc = publish_github.main()
        if rc == 0:
            print("✅ 已推送到 GitHub，Pages 将自动重建")
        else:
            print(f"⚠️ GitHub 推送未完成（rc={rc}）；面板仍可从 WorkBuddy 托管地址访问")
    except Exception as e:
        print(f"[warn] GitHub 推送异常: {e}")

    print("完成。")


if __name__ == "__main__":
    main()
