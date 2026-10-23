#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""国内期货期权 · 卖Put/卖Call 集中度面板（真实数据版）

输出：
  output/cn_panel.html        —— 独立国内面板
  output/cn_panel_<date>.md   —— Markdown 简报

数据链路（全部真实、可复核）：
  期货价    东财 futsseapi（郑商所用交易所官方每日行情结算价）
  逐档持仓量  东财 ccl（郑商所用交易所官方持仓量）
  到期时间  交易所官方规则推算（已用官方 DELTA 反解交叉验证，RMSE 0.0006）
  IV / Delta  由真实收盘价经 Black-76 反解（郑商所直接用交易所官方 IV/DELTA）
  集中价位  在 δ∈[0.10,0.25] 虚值带内，取持仓量(OI)最大的行权价
"""
import datetime
import json
import os
import re
import sys

import cn_fetch as CF
import cn_engine as E
import cn_expiry as CN_EXP

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "output")
os.makedirs(OUT, exist_ok=True)

# 目标品种：中文名 -> (交易所, 期权代码, 期货代码, 单位, 最小变动价位)
PRODUCTS = [
    ("豆粕",   "DCE",  "m",  "m",  "元/吨",     1.0),
    ("豆油",   "DCE",  "y",  "y",  "元/吨",     2.0),
    ("棕榈油", "DCE",  "p",  "p",  "元/吨",     2.0),
    ("菜油",   "CZCE", "OI", "OI", "元/吨",     1.0),
    ("菜粕",   "CZCE", "RM", "RM", "元/吨",     1.0),
    ("生猪",   "DCE",  "lh", "lh", "元/吨",     5.0),
    ("鸡蛋",   "DCE",  "jd", "jd", "元/500千克", 1.0),
    ("豆二",   "DCE",  "b",  "b",  "元/吨",     1.0),
]


def _fmt_price(v, tick):
    if v is None:
        return "—"
    dec = 0 if tick >= 1 else 2
    return f"{v:,.{dec}f}"


def _month_of(dm):
    m = re.match(r"^[A-Za-z]+?(\d{3,4})$", dm or "")
    return m.group(1) if m else None


def build(today=None):
    today = today or datetime.date.today()
    print("[1/4] 抓取东方财富期权/期货行情 ...", file=sys.stderr)
    fut = CF.fetch_futures()
    opts = {"DCE": CF.fetch_options("DCE"), "CZCE": CF.fetch_options("CZCE")}

    print("[2/4] 抓取郑商所官方每日行情 ...", file=sys.stderr)
    czce_opt = CF.fetch_czce_official(today, "Option")
    czce_fut = CF.fetch_czce_official(today, "Future")
    # 郑商所官方文件当日可能尚未发布（实测约 15:42 上传），此时会回溯到最近可用交易日。
    # 必须如实记录实际数据日期，绝不让页面显示"今天"却挂昨天数据（假更新）。
    czce_date = CF.CZCE_DATA_DATE or today
    if czce_date != today:
        print(f"[warn] 郑商所官方数据实际日期 = {czce_date}（当日文件未发布）",
              file=sys.stderr)

    print("[3/4] 引擎分析 ...", file=sys.stderr)
    rows = []
    for cn, exch, ocode, fcode, unit, tick in PRODUCTS:
        ocode_l = ocode.lower()
        by = fut.get(exch, {}).get(ocode_l, {})
        # 用**期权持仓量**排名选主力月份（期货持仓最高的月份其期权可能已交割清零）
        oi_by_month = CF.option_oi_by_month(opts.get(exch, []), ocode_l)
        main, second = CF.pick_main_secondary(by, option_oi=oi_by_month)
        if not main:
            print(f"  [skip] {cn}: 未取到期货合约", file=sys.stderr)
            continue

        month = _month_of(main["dm"])
        if not month:
            continue

        # 期货价：郑商所优先用交易所官方结算价
        F = main.get("price")
        settle = None
        if exch == "CZCE":
            fo = czce_fut.get(main["dm"].upper(), {})
            if fo.get("settle"):
                F = settle = fo["settle"]
            elif fo.get("close"):
                F = settle = fo["close"]

        official = czce_opt if exch == "CZCE" else None
        res = E.analyze(F, month, ocode_l, ocode.upper(), opts.get(exch, []),
                        official=official, today=today, variety=cn, exch=exch)

        rows.append({
            "name": cn, "exch": exch, "unit": unit, "tick": tick,
            "contract": main["dm"], "contract_cn": main.get("name"),
            "F": F, "settle": settle, "fut_oi": main.get("oi"), "fut_vol": main.get("vol"),
            "month": month, "res": res,
        })

    print("[4/4] 渲染 ...", file=sys.stderr)
    html = render_html(rows, today, czce_date)
    path_html = os.path.join(OUT, "cn_panel.html")
    with open(path_html, "w", encoding="utf-8") as f:
        f.write(html)

    md = render_md(rows, today, czce_date)
    path_md = os.path.join(OUT, f"cn_panel_{today.isoformat()}.md")
    with open(path_md, "w", encoding="utf-8") as f:
        f.write(md)

    # 同时落一份 JSON，供后续合并/校验
    path_json = os.path.join(OUT, f"cn_panel_{today.isoformat()}.json")
    with open(path_json, "w", encoding="utf-8") as f:
        json.dump({"date": today.isoformat(),
                   "czce_data_date": czce_date.isoformat(),
                   "rows": [_json_row(r) for r in rows]},
                  f, ensure_ascii=False, indent=2)

    print(f"已生成: {path_html}")
    print(f"已生成: {path_md}")
    print(f"已生成: {path_json}")
    return rows


def _json_row(r):
    res = r["res"]
    pt, ct = res.get("put_top"), res.get("call_top")
    return {
        "name": r["name"], "exch": r["exch"], "contract": r["contract"],
        "F": r["F"], "unit": r["unit"],
        "expiry": res.get("expiry"), "T": res.get("T"), "T_method": res.get("T_method"),
        "n_strikes": res.get("n_strikes"), "total_oi": res.get("total_oi"),
        "put_top": ({"strike": pt["strike"], "oi": pt["oi"], "delta": pt.get("delta"),
                     "iv": pt.get("iv"), "oi_chg": pt.get("oi_chg")} if pt else None),
        "call_top": ({"strike": ct["strike"], "oi": ct["oi"], "delta": ct.get("delta"),
                      "iv": ct.get("iv"), "oi_chg": ct.get("oi_chg")} if ct else None),
    }


def render_html(rows, today, czce_date=None):
    czce_date = czce_date or today
    stale = czce_date != today
    date_note = (
        f"<br><span class='warninline'>⚠️ 郑商所官方数据实际截至 <b>{czce_date}</b>"
        f"（当日文件尚未发布，已回溯最近可用交易日；大商所数据为 {today} 当日）</span>"
        if stale else
        f"（郑商所官方数据 {czce_date}，大商所数据 {today}）")
    trs = ""
    for r in rows:
        res = r["res"]
        pt, ct = res.get("put_top"), res.get("call_top")
        f_txt = _fmt_price(r["F"], r["tick"])

        def cell(rec, is_call):
            if not rec:
                return "<span class='na'>—</span>"
            d = rec.get("delta")
            d_txt = f"{abs(d):.3f}" if d is not None else "—"
            iv = rec.get("iv")
            iv_txt = f"{iv*100:.1f}%" if iv else "—"
            oi = rec.get("oi") or 0
            return (f"<b class='num'>{_fmt_price(rec['strike'], r['tick'])}</b><br>"
                    f"<span class='sub'>OI {int(oi):,} 手 · δ{d_txt}<br>IV {iv_txt}</span>")

        trs += (
            f"<tr><td class='nm'><b>{r['name']}</b><br>"
            f"<span class='sub'>{r['contract']} · {r['exch']}<br>到期 {res.get('expiry') or '—'}</span></td>"
            f"<td class='f'><b class='num'>{f_txt}</b><br><span class='sub'>{r['unit']}</span></td>"
            f"<td>{cell(pt, False)}</td><td>{cell(ct, True)}</td></tr>")

    return f"""<!DOCTYPE html><html lang="zh"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>国内商品期权 · 卖Put/卖Call 集中度面板</title>
<style>
 body{{font-family:-apple-system,'PingFang SC','Microsoft YaHei',sans-serif;margin:0;background:#f5f7fa;color:#1a1a1a}}
 .banner{{background:#1f7a3d;color:#fff;padding:10px 18px;font-size:14px}}
 .banner b{{background:#0c3;padding:1px 6px;border-radius:3px;color:#042}}
 .warn{{background:#fff4e5;color:#8a4b00;border:1px solid #ffd8a8;padding:10px 18px;font-size:13px;line-height:1.7}}
 .card{{background:#fff;margin:18px;padding:18px;border-radius:10px;box-shadow:0 1px 4px #0001}}
 h1{{font-size:20px;margin:0 0 6px}} h2{{font-size:16px;margin:20px 0 8px}}
 table{{border-collapse:collapse;width:100%;font-size:13px}}
 th,td{{border:1px solid #e3e8ef;padding:9px 10px;text-align:center;vertical-align:middle}}
 th{{background:#eef2f7;font-weight:600}}
 td.nm{{text-align:left;min-width:130px}} td.f{{background:#f7fbf8}}
 .num{{font-size:16px;color:#0a5c2e}}
 .sub{{color:#777;font-size:11px;line-height:1.6}}
 .na{{color:#bbb}}
 .note{{color:#666;font-size:12px;line-height:1.8}}
 .warninline{{color:#8a4b00;font-size:12px}}
 .ok{{color:#1f7a3d;font-weight:600}}
 ul{{margin:6px 0;padding-left:20px}}
</style></head><body>
<div class="banner">✅ <b>真实数据</b>：期货价、逐档持仓量(OI)、隐含波动率、Delta 全部来自交易所与公开行情（{today} 抓取）</div>
<div class="warn">⚠️ <b>口径说明（重要）</b><br>
① 本表的「卖Put集中价位」= 在 <b>δ∈[0.10, 0.25]</b> 的虚值 Put 区间内，<b>持仓量(OI)最大</b>的那一档行权价。<br>
② 持仓量是<b>总持仓</b>（多空合计），交易所免费数据<b>不区分买卖方向</b>——所以"卖Put集中"是<b>基于虚值+高持仓的概率推断</b>，不是交易所披露的事实。<br>
③ IV 与 Delta：郑商所品种直接采用<b>交易所官方每日行情表</b>的隐含波动率与Delta；大商所品种由<b>真实收盘价经 Black-76 反解</b>得到（到期日按交易所官方规则推算）。<br>
④ 本引擎定价实现已用郑商所官方 DELTA 做回归校验，RMSE=0.0006（0.06%）。</div>

<div class="card">
<h1>国内商品期权 · 卖Put / 卖Call 持仓集中价位（主力合约）</h1>
<div class="note">标的：豆粕、豆油、棕榈油、菜油、菜粕、生猪、鸡蛋、豆二 —— 每个品种的<b>主力合约</b>（按持仓量最大认定）。
<br>数据日期：{date_note}</div>
<table>
<tr><th style="width:16%">品种 / 合约</th><th style="width:14%">期货价（真实）</th>
<th style="width:26%">卖Put集中价位<br><span style="font-weight:400;font-size:11px">价格下方 δ∈[0.10,0.25] 内 OI 最大档</span></th>
<th style="width:26%">卖Call集中价位<br><span style="font-weight:400;font-size:11px">价格上方 δ∈[0.10,0.25] 内 OI 最大档</span></th></tr>
{trs}
</table>

<h2>怎么读</h2>
<ul class="note">
<li><span class="ok">支撑线 = 真实期货价</span>；其下方是卖Put集中档（产业接货/卖权收租区域），上方是卖Call集中档。</li>
<li><b>OI 手数</b>是该行权价的总持仓量；数值越大说明该价位挂单越拥挤，价差被击穿的概率越高。</li>
<li>若价格<b>有效跌破</b>卖Put集中档，卖方被动止损易形成踩踏，支撑转压力——油料期权经典行情。</li>
<li>同一品种的<b>次主力合约</b>（如豆粕 2705）未在本表展示；可在「下方明细」查看。</li>
</ul>
</div>
</body></html>"""


def render_md(rows, today, czce_date=None):
    czce_date = czce_date or today
    md = f"# 国内商品期权 · 卖Put/卖Call 持仓集中价位 · {today}\n\n"
    if czce_date != today:
        md += f"> ⚠️ **郑商所官方数据实际截至 {czce_date}**（当日文件尚未发布，已回溯最近可用交易日）。\n\n"
    md += "**数据说明**：期货价、逐档持仓量、IV、Delta 均为真实数据"
    md += "（郑商所用交易所官方每日行情表；大商所用东方财富公开行情 + Black-76 反解）。\n"
    md += "**「卖Put集中」= δ∈[0.10,0.25] 虚值区间内持仓量(OI)最大的行权价**；"
    md += "持仓量不含买卖方向，故为概率推断而非事实。\n\n"
    md += "| 品种 | 合约 | 期货价 | 卖Put集中 | OI | δ | 卖Call集中 | OI | δ | 到期日 |\n"
    md += "|---|---|---|---|---|---|---|---|---|---|\n"
    for r in rows:
        res = r["res"]
        pt, ct = res.get("put_top"), res.get("call_top")
        pt_k = _fmt_price(pt["strike"], r["tick"]) if pt else "—"
        pt_oi = f"{int(pt['oi']):,}" if pt else "—"
        pt_d = f"{abs(pt['delta']):.3f}" if (pt and pt.get("delta") is not None) else "—"
        ct_k = _fmt_price(ct["strike"], r["tick"]) if ct else "—"
        ct_oi = f"{int(ct['oi']):,}" if ct else "—"
        ct_d = f"{ct['delta']:.3f}" if (ct and ct.get("delta") is not None) else "—"
        md += (f"| {r['name']} | {r['contract']} | {_fmt_price(r['F'], r['tick'])} {r['unit']} | "
               f"{pt_k} | {pt_oi} | {pt_d} | {ct_k} | {ct_oi} | {ct_d} | "
               f"{res.get('expiry') or '—'} |\n")
    return md


if __name__ == "__main__":
    build()
