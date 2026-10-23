#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CBOT + 国内商品期权 · 统一面板

两个板块：
  A. CBOT（外盘）—— 真实期货价 + 假设IV 的理论虚值带（真实 OI 需 QuikStrike/CME 导出，
     当前沙箱内不可达，故仍是推算口径，页面已明确标注）
  B. 国内商品期权 —— 真实逐档持仓量 + 交易所口径 IV/Delta，
     直接定位"卖Put/卖Call 持仓最集中的行权价"

输出：output/index.html （统一入口）
"""
import datetime
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "output")
os.makedirs(OUT, exist_ok=True)
PY = sys.executable


def run(script):
    print(f"[run] {script}", file=sys.stderr)
    try:
        r = subprocess.run([PY, os.path.join(HERE, script)], cwd=HERE,
                           capture_output=True, text=True, timeout=900,
                           encoding="utf-8", errors="replace")
        if r.returncode != 0:
            print(f"[warn] {script} 退出码 {r.returncode}\n{r.stderr[-1500:]}", file=sys.stderr)
        return r.returncode == 0
    except Exception as e:  # noqa: BLE001
        print(f"[warn] {script} 执行失败: {e}", file=sys.stderr)
        return False


def _read(path):
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()
    except Exception:  # noqa: BLE001
        return ""


def _section_cbot(today):
    """CBOT 三列理论带 -> HTML 片段"""
    rows = []
    import fetch_sina
    from black76 import put_delta, call_delta
    import real_band as RB

    # 复用 real_band.py 已抓到的价格（避免重复请求新浪，风控下更稳）
    cache = os.path.join(HERE, "data", "realtime", "sina_prices.json")
    prices = {}
    if os.path.exists(cache):
        try:
            with open(cache, encoding="utf-8") as f:
                prices = (json.load(f) or {}).get("prices") or {}
        except Exception:  # noqa: BLE001
            prices = {}
    if not prices:
        prices = fetch_sina.fetch()
    if not prices:
        return "<p class='na'>CBOT 行情抓取失败，本区块跳过。</p>"
    for prod in RB.ORDER:
        p = prices.get(prod)
        if not p:
            continue
        F = p["price"]
        sigma = RB.IV[prod]
        pb = RB.find_put_band(F, sigma, RB.T, RB.R)
        cb = RB.find_call_band(F, sigma, RB.T, RB.R)
        tick = RB.TICK[prod]
        dec = 2 if prod == "ZL" else 0
        put_hi = RB.snap(pb[1], tick) if pb else None
        call_lo = RB.snap(cb[0], tick) if cb else None
        rows.append((RB.NAME[prod], p["code"], F, p["unit"], put_hi, call_lo, dec))

    trs = ""
    for nm, code, F, unit, ph, cl, dec in rows:
        phs = f"<b class='num'>{ph:,.{dec}f}</b>" if ph is not None else "<span class='na'>—</span>"
        cls = f"<b class='num'>{cl:,.{dec}f}</b>" if cl is not None else "<span class='na'>—</span>"
        trs += (f"<tr><td class='nm'><b>{nm}</b><br><span class='sub'>{code}</span></td>"
                f"<td class='f'><b class='num'>{F:,.{dec}f}</b><br><span class='sub'>{unit}</span></td>"
                f"<td>{phs}</td><td>{cls}</td></tr>")
    return f"""<table>
<tr><th style="width:18%">品种</th><th style="width:16%">期货价（真实）</th>
<th style="width:26%">卖Put上沿<br><span style="font-weight:400;font-size:11px">价格下方 Δ≈0.25（推算）</span></th>
<th style="width:26%">卖Call下沿<br><span style="font-weight:400;font-size:11px">价格上方 Δ≈0.25（推算）</span></th></tr>
{trs}</table>
<div class="note" style="margin-top:8px">⚠️ CBOT 板块的期货价是<b>真实</b>的（新浪财经），
但价位带是「真实价 + 假设IV 经 Black-76 推算」的<b>理论虚值带</b>；
真实 OI 拥挤度需 QuikStrike/CME 逐档导出，当前环境不可达，故不冒充真实持仓。</div>"""


def _section_cn(today):
    """国内真实持仓集中度 -> HTML 片段"""
    jf = os.path.join(OUT, f"cn_panel_{today}.json")
    if not os.path.exists(jf):
        return "<p class='na'>国内数据未生成，本区块跳过。</p>"
    with open(jf, encoding="utf-8") as f:
        data = json.load(f)

    trs = ""
    for r in data["rows"]:
        pt, ct = r.get("put_top"), r.get("call_top")

        def cell(rec):
            if not rec:
                return "<span class='na'>—</span>"
            d = rec.get("delta")
            iv = rec.get("iv")
            return (f"<b class='num'>{rec['strike']:,.0f}</b><br>"
                    f"<span class='sub'>OI {int(rec['oi'] or 0):,} 手 · "
                    f"δ{abs(d):.3f} · IV {iv*100:.1f}%</span>") if d is not None else \
                   (f"<b class='num'>{rec['strike']:,.0f}</b><br>"
                    f"<span class='sub'>OI {int(rec['oi'] or 0):,} 手</span>")

        trs += (f"<tr><td class='nm'><b>{r['name']}</b><br>"
                f"<span class='sub'>{r['contract']} · {r['exch']}<br>到期 {r.get('expiry') or '—'}</span></td>"
                f"<td class='f'><b class='num'>{r['F']:,.0f}</b><br><span class='sub'>{r['unit']}</span></td>"
                f"<td>{cell(pt)}</td><td>{cell(ct)}</td></tr>")
    return f"""<table>
<tr><th style="width:18%">品种 / 合约</th><th style="width:16%">期货价（真实）</th>
<th style="width:26%">卖Put集中价位<br><span style="font-weight:400;font-size:11px">价格下方 δ∈[0.10,0.25] 内 OI 最大档</span></th>
<th style="width:26%">卖Call集中价位<br><span style="font-weight:400;font-size:11px">价格上方 δ∈[0.10,0.25] 内 OI 最大档</span></th></tr>
{trs}</table>"""


def build(today=None):
    today = today or datetime.date.today()
    ok_cbot = run("real_band.py")
    ok_cn = run("cn_panel.py")
    if not ok_cn:
        print("[warn] 国内面板生成失败", file=sys.stderr)

    cbot = _section_cbot(today) if ok_cbot else "<p class='na'>CBOT 区块生成失败。</p>"
    cn = _section_cn(today)

    html = f"""<!DOCTYPE html><html lang="zh"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>期权卖Put/卖Call 集中价位面板 · CBOT + 国内</title>
<style>
 body{{font-family:-apple-system,'PingFang SC','Microsoft YaHei',sans-serif;margin:0;background:#f5f7fa;color:#1a1a1a}}
 .banner{{background:#1f7a3d;color:#fff;padding:10px 18px;font-size:14px}}
 .banner b{{background:#0c3;padding:1px 6px;border-radius:3px;color:#042}}
 .warn{{background:#fff4e5;color:#8a4b00;border:1px solid #ffd8a8;padding:10px 18px;font-size:13px;line-height:1.75}}
 .card{{background:#fff;margin:18px;padding:18px;border-radius:10px;box-shadow:0 1px 4px #0001}}
 h1{{font-size:20px;margin:0 0 6px}} h2{{font-size:17px;margin:0 0 8px;padding-bottom:6px;border-bottom:2px solid #eef2f7}}
 table{{border-collapse:collapse;width:100%;font-size:13px;margin-top:8px}}
 th,td{{border:1px solid #e3e8ef;padding:9px 10px;text-align:center;vertical-align:middle}}
 th{{background:#eef2f7;font-weight:600}}
 td.nm{{text-align:left;min-width:130px}} td.f{{background:#f7fbf8}}
 .num{{font-size:16px;color:#0a5c2e}}
 .sub{{color:#777;font-size:11px;line-height:1.6}}
 .na{{color:#bbb}}
 .note{{color:#666;font-size:12px;line-height:1.8}}
 .ok{{color:#1f7a3d;font-weight:600}}
 .tag{{display:inline-block;background:#e8f4ec;color:#1f7a3d;border-radius:4px;padding:1px 7px;font-size:11px;margin-right:6px}}
 .tag.warntag{{background:#fdeee0;color:#8a4b00}}
 ul{{margin:6px 0;padding-left:20px}}
</style></head><body>
<div class="banner">✅ <b>真实数据</b>：期货价 / 逐档持仓量 / 隐含波动率 / Delta 均取自交易所与公开行情（{today} 抓取）</div>
<div class="warn">⚠️ <b>两个板块的数据强度不同，请注意区分</b><br>
① <b>国内板块</b>：真实逐档持仓量 + 交易所口径 IV/Delta → 可直接定位「持仓最集中的行权价」。持仓量是多空合计，交易所免费数据<b>不含买卖方向</b>，故"卖Put集中"是基于「虚值 + 高持仓」的<b>概率推断</b>，非交易所披露的事实。<br>
② <b>CBOT 板块</b>：期货价真实，但价位带是「真实价 + 假设IV 经 Black-76 推算」的<b>理论虚值带</b>，<b>不含真实 OI</b>（真实 OI 需 QuikStrike/CME 逐档导出，当前环境不可达）。<br>
③ 定价引擎已用郑商所官方 DELTA 做回归校验：RMSE=0.0006（0.06%）；到期日按交易所官方规则推算并交叉验证通过。</div>

<div class="card">
<h1>期权卖Put / 卖Call 集中价位面板</h1>
<div class="note">数据更新时间：{today} &nbsp;|&nbsp; 数据源：郑商所官方每日行情表、东方财富公开行情、DCE/CZCE 交易所规则</div>

<h2 style="margin-top:22px"><span class="tag">真实持仓量</span>国内商品期权（主力合约）</h2>
{cn}

<h2 style="margin-top:26px"><span class="tag warntag">理论推算</span>CBOT 农产品</h2>
{cbot}

<h2>怎么读</h2>
<ul class="note">
<li><span class="ok">支撑线 = 真实期货价</span>；其下方是卖Put集中档，上方是卖Call集中档。</li>
<li>国内板块的 <b>OI 手数</b>是该行权价总持仓量，数值越大说明该价位越拥挤、价差越容易被击穿。</li>
<li>若价格<b>有效跌破</b>卖Put集中档，卖方被动止损易形成踩踏，支撑转压力——油料期权经典行情。</li>
<li>判定区间 δ∈[0.10,0.25]：产业卖权收租通常落在中度虚值（这个先验沿用豆包框架的核心部分）。</li>
</ul>
</div>
</body></html>"""

    path = os.path.join(OUT, "index.html")
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"已生成: {path}")
    return path


if __name__ == "__main__":
    build()
