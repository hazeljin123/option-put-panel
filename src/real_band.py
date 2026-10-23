#!/usr/bin/env python3
# 真实行情 · 卖Put/卖Call 理论价位带面板（精简版）
# 输入：新浪真实期货价（fetch_sina）+ 假设IV + Black-76 计算
# 输出：output/real_band.html 与 output/real_band_<date>.md
# 重要：本面板"期货价是真实的"，"价位带是用真实价+假设IV经Black-76推算的"，
#       它定位"卖权收租理论上应落在哪个虚值带"，并非"真实OI拥挤度"（那需QuikStrike/CME）。
import json, os, datetime, sys
from black76 import put_delta, call_delta
import fetch_sina

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "output")
os.makedirs(OUT, exist_ok=True)

R = 0.045            # 无风险利率(USD, 近似)
T = 45.0 / 365.0     # 近月期权到期≈45天(近似)
# 假设IV（近一年历史波动率近似，非实时隐含波动率，明显标注）
IV = {"ZS": 0.22, "ZM": 0.28, "ZC": 0.25, "ZL": 0.30}
# CME 行权价最小刻度（用于把理论带对齐到实际可交易档）
TICK = {"ZS": 5.0, "ZM": 5.0, "ZC": 5.0, "ZL": 0.5}
NAME = {"ZS": "CBOT大豆", "ZM": "CBOT豆粕", "ZC": "CBOT玉米", "ZL": "CBOT豆油"}
ORDER = ["ZS", "ZM", "ZC", "ZL"]


def to_usd(prod, val):
    """把 native 单位换算成更直观的美元报价，便于阅读。"""
    if prod in ("ZS", "ZC"):          # cents/bu -> $/bu
        return f"${val/100:.2f}/bu"
    if prod == "ZL":                  # cents/lb -> $/lb
        return f"${val/100:.3f}/lb"
    if prod == "ZM":                  # $/ton
        return f"${val:.1f}/ton"
    return f"{val:.2f}"


def find_put_band(F, sigma, T, r, lo=0.10, hi=0.25):
    """扫描行权价，找 |Put Delta| 落在 [lo, hi] 的区间（价格下方 OTM 虚值带）。"""
    ks = []
    K = 0.55 * F
    step = 0.0004 * F
    while K < 0.999 * F:
        d = abs(put_delta(F, K, T, r, sigma))
        if lo <= d <= hi:
            ks.append(K)
        K += step
    if not ks:
        return None
    return min(ks), max(ks)   # (δ0.10 深虚低档, δ0.25 近ATM高档)


def find_call_band(F, sigma, T, r, lo=0.10, hi=0.25):
    """扫描行权价，找 Call Delta 落在 [lo, hi] 的区间（价格上方 OTM 虚值带）。"""
    ks = []
    K = 1.001 * F
    step = 0.0004 * F
    while K < 1.6 * F:
        d = call_delta(F, K, T, r, sigma)
        if lo <= d <= hi:
            ks.append(K)
        K += step
    if not ks:
        return None
    return min(ks), max(ks)   # (δ0.25 近ATM低档, δ0.10 深虚高档)


def snap(v, tick):
    return round(v / tick) * tick


def main():
    prices = fetch_sina.fetch()
    if not prices:
        print("新浪抓取失败，无法生成真实价位带。", file=sys.stderr)
        raise SystemExit(2)
    # 落盘缓存，供 build_index.py 复用（避免重复请求新浪）
    try:
        cache_dir = os.path.join(HERE, "data", "realtime")
        os.makedirs(cache_dir, exist_ok=True)
        with open(os.path.join(cache_dir, "sina_prices.json"), "w", encoding="utf-8") as fp:
            json.dump({"updated": datetime.date.today().isoformat(),
                       "prices": prices}, fp, ensure_ascii=False, indent=2)
    except Exception as e:  # noqa: BLE001
        print(f"[warn] 价格缓存写入失败: {e}", file=sys.stderr)
    today = datetime.date.today().isoformat()
    rows = []
    for prod in ORDER:
        p = prices[prod]
        F = p["price"]
        sigma = IV[prod]
        pb = find_put_band(F, sigma, T, R)
        cb = find_call_band(F, sigma, T, R)
        rec = {"prod": prod, "name": NAME[prod], "code": p["code"], "cname": p["name"],
               "F": F, "unit": p["unit"], "sigma": sigma,
               "F_usd": to_usd(prod, F),
               "put_hi": None, "call_lo": None}
        if pb:
            # 卖Put上沿 = δ0.25 近ATM高档（价格下方、最靠近平值）
            rec["put_hi"] = snap(pb[1], TICK[prod])
            rec["put_hi_usd"] = to_usd(prod, pb[1])
        if cb:
            # 卖Call下沿 = δ0.25 近ATM低档（价格上方、最靠近平值）
            rec["call_lo"] = snap(cb[0], TICK[prod])
            rec["call_lo_usd"] = to_usd(prod, cb[0])
        rows.append(rec)

    # ---------- 渲染 HTML ----------
    html_rows = ""
    for r in rows:
        ph = (f"<td><b>{r['put_hi']:.{2 if r['prod']=='ZL' else 0}f}</b><br>"
              f"<span style='color:#666'>{r.get('put_hi_usd','')}</span></td>"
              ) if r["put_hi"] is not None else "<td style='color:#b00'>—</td>"
        cl = (f"<td><b>{r['call_lo']:.{2 if r['prod']=='ZL' else 0}f}</b><br>"
              f"<span style='color:#666'>{r.get('call_lo_usd','')}</span></td>"
              ) if r["call_lo"] is not None else "<td style='color:#b00'>—</td>"
        html_rows += (
            f"<tr><td><b>{r['name']}</b><br><span style='color:#666'>{r['cname']} · {r['code']}</span></td>"
            f"<td><b>{r['F']:.{2 if r['prod']=='ZL' else 1}f}</b><br><span style='color:#666'>{r['F_usd']}</span></td>"
            f"{ph}{cl}</tr>")

    html = f"""<!DOCTYPE html><html lang="zh"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>CBOT 卖Put/卖Call 理论价位带 · 真实行情</title>
<style>
 body{{font-family:-apple-system,'PingFang SC','Microsoft YaHei',sans-serif;margin:0;background:#f5f7fa;color:#1a1a1a}}
 .banner{{background:#1f7a3d;color:#fff;padding:10px 18px;font-size:14px}}
 .banner b{{background:#0c3;padding:1px 6px;border-radius:3px;color:#042}}
 .warn{{background:#fff4e5;color:#8a4b00;border:1px solid #ffd8a8;padding:10px 18px;font-size:13px}}
 .card{{background:#fff;margin:18px;padding:18px;border-radius:10px;box-shadow:0 1px 4px #0001}}
 h1{{font-size:20px;margin:0 0 4px}} h2{{font-size:16px;margin:18px 0 8px}}
 table{{border-collapse:collapse;width:100%;font-size:13px}}
 th,td{{border:1px solid #e3e8ef;padding:8px 10px;text-align:center;vertical-align:middle}}
 th{{background:#eef2f7}} td:nth-child(1){{text-align:left}}
 .note{{color:#666;font-size:12px;line-height:1.6}}
 .ok{{color:#1f7a3d;font-weight:600}}
</style></head><body>
<div class="banner">✅ <b>真实行情</b>：期货价来自新浪财经（免费公开，{today} 抓取） &nbsp;|&nbsp;
理论价位带 = 真实价 + 假设IV 经 Black-76 推算 <b>非真实 OI 拥挤度</b></div>
<div class="warn">⚠️ 诚实声明：本面板给出的是「卖权收租理论上应落在哪个虚值价位带」，由<b>真实期货价</b>推算；
它<b>不是</b>「哪个价位 OI 最集中」——后者必须真实逐档持仓量（QuikStrike/CME 导出）。IV 为历史波动近似假设，带会随 IV/到期日变化而平移。</div>
<div class="card">
<h1>CBOT 农产品 · 卖Put上沿 / 卖Call下沿（OTM Δ≈0.25，价格两侧收租区）</h1>
<div class="note">判定逻辑（沿用豆包框架的核心先验）：产业卖权收租几乎都在中度虚值（Δ 0.10–0.25）挂单。
下列「卖Put上沿」= 价格下方、最靠近平值的 OTM Put（Δ≈0.25）；「卖Call下沿」= 价格上方、最靠近平值的 OTM Call（Δ≈0.25）。
两者夹住真实期货价，构成「产业愿意接货/卖权收租」的理论边界。</div>
<table>
<tr><th>品种</th><th>支撑线·真实期货价<br>(新浪 {today})</th><th>卖Put上沿<br>(价格下方 Δ≈0.25)</th><th>卖Call下沿<br>(价格上方 Δ≈0.25)</th></tr>
{html_rows}
</table>
<h2>怎么读</h2>
<ul class="note">
<li><span class="ok">绿色横幅=真实数据</span>：期货价是新浪实时/延迟真实报价；价位带是用它+期权定价模型算出的<b>位置</b>，不是编造的 OI 数字。</li>
<li><b>支撑线=真实期货价</b>；其下方「卖Put上沿」、上方「卖Call下沿」是卖权收租最密集的理论边界（δ0.25）。整条虚值带向更深处延伸到 δ0.10。</li>
<li>要到"真实最集中价位"，还需把 QuikStrike 的 OI-by-strike 导出喂进来——届时本面板的"真实期货价"不变，额外叠加真实的 OI 拥挤度评分。</li>
<li>若价格<b>有效跌破</b>卖Put带，卖方被动止损易踩踏，支撑变压力（油料期权经典行情）。</li>
</ul>
<div class="note">方法学：无风险利率 {R*100:.1f}%，近月期权到期 {int(T*365)} 天近似；IV 为近一年历史波动率近似（大豆22%/豆粕28%/玉米25%/豆油30%）。
精确到合约月份(NOV/JAN)的带会略有偏移，本版用主连价近似。</div>
</div>
</body></html>"""

    path_html = os.path.join(OUT, "real_band.html")
    with open(path_html, "w", encoding="utf-8") as f:
        f.write(html)

    # ---------- 渲染 Markdown 简报 ----------
    md = f"# CBOT 卖Put上沿 / 卖Call下沿（真实行情）· {today}\n\n"
    md += "**数据说明**：期货价 = 新浪财经真实报价（免费公开）；价位带 = 真实价 + 假设IV 经 Black-76 推算的 **理论虚值带（Δ≈0.25）**，**非真实 OI 拥挤度**。\n\n"
    md += "| 品种 | 支撑线·真实期货价 | 卖Put上沿(价格下方 Δ≈0.25) | 卖Call下沿(价格上方 Δ≈0.25) |\n"
    md += "|---|---|---|---|\n"
    for r in rows:
        ph = f"{r['put_hi']:.{2 if r['prod']=='ZL' else 0}f} ({r.get('put_hi_usd','')})" if r["put_hi"] is not None else "—"
        cl = f"{r['call_lo']:.{2 if r['prod']=='ZL' else 0}f} ({r.get('call_lo_usd','')})" if r["call_lo"] is not None else "—"
        md += f"| {r['name']}({r['cname']}) | {r['F']:.{2 if r['prod']=='ZL' else 1}f} {r['unit']} ({r['F_usd']}) | {ph} | {cl} |\n"
    md += "\n> ⚠️ 这是『卖权收租价位带位置』的理论估计（真实期货价+Black-76），不是『哪个价位 OI 最集中』。真实拥挤度需 QuikStrike/CME 逐档 OI 导出。\n"

    path_md = os.path.join(OUT, f"real_band_{today}.md")
    with open(path_md, "w", encoding="utf-8") as f:
        f.write(md)

    print(f"已生成: {path_html}\n已生成: {path_md}")
    print(md)


if __name__ == "__main__":
    main()
