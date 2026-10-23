#!/usr/bin/env python3
# CBOT 卖Put集中度流水线：
#   摄入 QuikStrike/CME VOI 逐档 Put OI 导出 -> 主力/次主力识别 -> 改进5步打分 -> 聚类支撑带 -> HTML面板 + 每日md报告
import csv, json, os, glob, datetime
from collections import Counter

BASE = os.path.dirname(os.path.abspath(__file__))
INCOMING = os.path.join(BASE, "data", "incoming")
PROCESSED = os.path.join(BASE, "data", "processed")
OUTPUT = os.path.join(BASE, "output")
os.makedirs(PROCESSED, exist_ok=True)
os.makedirs(OUTPUT, exist_ok=True)

ALIASES = {
    "product": ["product", "Product", "Symbol", "代码"],
    "expiry": ["expiry", "Expiry", "Month", "Contract", "到期月"],
    "strike": ["strike", "Strike", "Strike Price", "行权价"],
    "type": ["type", "Type", "Put/Call", "PC", "方向"],
    "oi": ["oi", "OI", "Open Interest", "OpenInterest", "持仓"],
    "oi_change": ["oi_change", "Change", "ΔOI", "OI Change", "OI变动"],
    "delta": ["delta", "Delta", "德尔塔"],
    "iv": ["iv", "IV", "Implied Vol", "ImpliedVol", "隐波"],
    "volume": ["volume", "Volume", "Vol", "成交量"],
    "underlying": ["underlying", "Underlying", "Future", "Futures Price", "期货价"],
    "date": ["date", "Date", "Trade Date", "交易日"],
}


def _get(row, key):
    for a in ALIASES[key]:
        if a in row and row[a] not in (None, ""):
            return row[a]
    return None


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def load_all():
    # 返回 history[(product,expiry,strike,type)][date] = rec
    history = {}
    dates = set()
    files = sorted(glob.glob(os.path.join(INCOMING, "*.csv")))
    for fp in files:
        with open(fp, newline="", encoding="utf-8-sig") as f:
            for row in csv.DictReader(f):
                prod = _get(row, "product")
                exp = _get(row, "expiry")
                strike = _get(row, "strike")
                typ = _get(row, "type")
                if not prod or strike is None:
                    continue
                typ = (typ or "P").upper()
                if typ not in ("P", "C"):
                    typ = "P" if str(typ).startswith("P") else "C"
                date = _get(row, "date") or os.path.basename(fp).split("_")[-1].split(".")[0]
                key = (str(prod).strip(), str(exp).strip(), float(strike), typ)
                rec = {
                    "oi": _num(_get(row, "oi")),
                    "oi_change": _num(_get(row, "oi_change")),
                    "delta": _num(_get(row, "delta")),
                    "iv": _num(_get(row, "iv")),
                    "volume": _num(_get(row, "volume")),
                    "underlying": _num(_get(row, "underlying")),
                }
                history.setdefault(key, {})[date] = rec
                dates.add(date)
    return history, sorted(dates)


def score_strike(rec_latest, hist_dates, oi_series, iv_series, cfg):
    delta = rec_latest.get("delta")
    dmin, dmax = cfg["delta_min"], cfg["delta_max"]
    # Step1 OTM delta 带
    if delta is None:
        s1 = None
    else:
        s1 = (dmin <= delta <= dmax)
    # Step2 连续上升天数（基于历史OI）
    trail = 0
    for i in range(len(oi_series) - 1, 0, -1):
        if oi_series[i] is not None and oi_series[i - 1] is not None and oi_series[i] > oi_series[i - 1]:
            trail += 1
        else:
            break
    s2 = trail >= cfg["min_days_rise"]
    # Step3 IV 不跟涨（末日 vs 前一日）
    iv_chg = None
    if len(iv_series) >= 2 and iv_series[-1] is not None and iv_series[-2] is not None:
        iv_chg = iv_series[-1] - iv_series[-2]
    s3 = None if iv_chg is None else (iv_chg <= cfg["iv_change_tol"])
    # Step4 成交结构：标准OI导出无逐笔/大单字段 -> 数据缺失，标记N/A
    s4 = None
    # Step5 价格支撑近似：期货在行权价上方且距离 <= near_strike_pct
    und = rec_latest.get("underlying")
    strike = rec_latest.get("strike")
    s5 = None
    if und and strike and und > strike:
        s5 = ((und - strike) / und) <= cfg["near_strike_pct"]

    comps = {"delta_band": s1, "oi_rise": s2, "iv_flat": s3, "vol_passive": s4, "support": s5}
    ntrue = sum(1 for v in comps.values() if v is True)
    nna = sum(1 for v in comps.values() if v is None)
    delta_ok = (s1 is True)
    return {
        "comps": comps, "ntrue": ntrue, "nna": nna,
        "trail_days": trail, "iv_chg": iv_chg, "delta_ok": delta_ok,
    }


def verdict(comps):
    # 基于"可评估的核心三信号"定性：①OTM delta带 ②OI连续上升 ③IV不跟涨
    # ④成交结构在标准OI导出中恒为N/A；⑤价格支撑为条件性加分
    s1, s2, s3, s4, s5 = (comps["delta_band"], comps["oi_rise"], comps["iv_flat"],
                           comps["vol_passive"], comps["support"])
    if s1 is None:
        return ("数据不足·缺Delta", "gray")
    if s1 is False:
        return ("排除·偏买Put/近ATM", "red")
    if s2 and s3:
        return ("推断·卖Put高概率带", "blue")
    if s2 or s3:
        return ("推断·中度集中", "teal")
    return ("弱/不确定", "gray")


COLOR = {"blue": ("#e8f1ff", "#1a4f9c"), "teal": ("#e6f7f4", "#0f6b5c"),
         "gray": ("#f1f1f1", "#666"), "red": ("#fdecea", "#c0392b")}


def select_contracts(product, latest_date, history, cfg):
    # 按最新日各 expiry 的 put 总 OI 选前二
    agg = {}
    for (p, exp, strike, typ), dmap in history.items():
        if p != product or typ != "P":
            continue
        rec = dmap.get(latest_date)
        if rec and rec.get("oi") is not None:
            agg[exp] = agg.get(exp, 0) + rec["oi"]
    ranked = sorted(agg.items(), key=lambda x: -x[1])
    if cfg.get("contract_selection") == "manual" and cfg.get("manual_expiries", {}).get(product):
        manual = cfg["manual_expiries"][product]
        chosen = manual[:2]
    else:
        chosen = [e for e, _ in ranked[:2]]
    return chosen


def run(date=None):
    cfg = json.load(open(os.path.join(BASE, "config.json"), encoding="utf-8"))
    history, all_dates = load_all()
    if not all_dates:
        print("ERROR: 没有找到任何数据，请先把 QuikStrike/CME OI 导出放到 data/incoming/")
        return None
    latest = date or all_dates[-1]

    results = {}  # product -> expiry -> [row]
    cluster_report = []
    for prod in cfg["products"]:
        exps = select_contracts(prod, latest, history, cfg)
        results[prod] = {}
        for rank, exp in enumerate(exps):
            label = "主力" if rank == 0 else "次主力"
            rows = []
            ticks = []
            for (p, e, strike, typ), dmap in history.items():
                if p != prod or e != exp or typ != "P":
                    continue
                rec = dmap.get(latest)
                if not rec:
                    continue
                ds = sorted(dmap.keys())
                oi_series = [dmap[d].get("oi") for d in ds]
                iv_series = [dmap[d].get("iv") for d in ds]
                sc = score_strike(rec, ds, oi_series, iv_series, cfg["scoring"])
                vt, vcolor = verdict(sc["comps"])
                rows.append({
                    "strike": strike, "delta": rec.get("delta"),
                    "oi": rec.get("oi"), "oi_change": rec.get("oi_change"),
                    "iv": rec.get("iv"), "iv_chg": sc["iv_chg"],
                    "trail": sc["trail_days"],
                    "ntrue": sc["ntrue"], "nna": sc["nna"],
                    "comps": sc["comps"], "verdict": vt, "color": vcolor,
                    "underlying": rec.get("underlying"),
                })
                ticks.append(strike)
            rows.sort(key=lambda r: (-r["ntrue"], -(r["nna"] or 0), -(r["oi"] or 0)))
            results[prod][(exp, label)] = rows
            # 聚类支撑带：用"众数档距"判定相邻档（避免异常半档距干扰）
            band_rows = [r for r in rows if r["color"] in ("blue", "teal")]
            band_rows.sort(key=lambda r: r["strike"])
            uniq = sorted(set(ticks))
            diffs = [uniq[i] - uniq[i - 1] for i in range(1, len(uniq)) if uniq[i] != uniq[i - 1]]
            tick = Counter(diffs).most_common(1)[0][0] if diffs else 1
            bands = []
            cur = []
            for r in band_rows:
                if cur and r["strike"] - cur[-1]["strike"] <= tick:
                    cur.append(r)
                else:
                    if cur:
                        bands.append(cur)
                    cur = [r]
            if cur:
                bands.append(cur)
            for b in bands:
                bands_oi = sum((r["oi"] or 0) for r in b)
                cluster_report.append({
                    "product": prod, "expiry": exp, "label": label,
                    "lo": b[0]["strike"], "hi": b[-1]["strike"],
                    "n": len(b), "oi": bands_oi,
                    "scores": "/".join(f'{r["ntrue"]}' for r in b),
                })
    # 写 processed 快照
    json.dump({"latest": latest, "results": {f"{prod}": {f"{e}|{l}": v for (e, l), v in d.items()} for prod, d in results.items()}},
              open(os.path.join(PROCESSED, f"{latest}.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    html = render_html(cfg, latest, all_dates, results, cluster_report)
    md = render_md(cfg, latest, results, cluster_report)
    html_path = os.path.join(OUTPUT, "dashboard.html")
    md_path = os.path.join(OUTPUT, f"report_{latest}.md")
    open(html_path, "w", encoding="utf-8").write(html)
    open(md_path, "w", encoding="utf-8").write(md)
    print("OK latest=", latest, "dates=", all_dates)
    print("HTML ->", html_path)
    print("MD   ->", md_path)
    return {"html": html_path, "md": md_path, "latest": latest}


def render_html(cfg, latest, all_dates, results, cluster_report):
    demo_banner = ""
    if cfg.get("demo"):
        demo_banner = "<div style='background:#c0392b;color:#fff;padding:10px 14px;font-size:14px;font-weight:600;border-radius:6px;margin-bottom:16px'>⚠️ 演示数据 · 非真实行情 — 当前为构造样张，仅用于验证逻辑；真实数据投喂后自动移除本横幅。</div>"
    pname = cfg["products"]
    fresh = f"{latest}（数据覆盖 {all_dates[0]} ~ {all_dates[-1]}，共 {len(all_dates)} 个交易日）"
    bands_html = ""
    if cluster_report:
        rows = "".join(
            f"<tr><td>{pname[b['product']]['name']}</td><td>{b['label']} {b['expiry']}</td>"
            f"<td><b>{b['lo']}–{b['hi']}</b></td><td>{b['n']} 档</td>"
            f"<td>{int(b['oi']):,}</td><td>{b['scores']}</td></tr>"
            for b in cluster_report)
        bands_html = f"<h2>① 卖Put支撑带总览</h2><table class='t'><tr><th>产品</th><th>合约</th><th>价位区间</th><th>档数</th><th>累计OI</th><th>各档分</th></tr>{rows}</table>"
    else:
        bands_html = "<h2>① 卖Put支撑带总览</h2><p>本日无满足高度/中度集中的支撑带。</p>"

    sec = ""
    for prod in cfg["products"]:
        sec += f"<h2>② {pname[prod]['name']}（{prod}）</h2>"
        for (exp, label), rows in results[prod].items():
            sec += f"<h3>{label}合约 · {exp}　期货参考价 {rows[0]['underlying'] if rows else '—'} {pname[prod]['unit']}</h3>"
            body = ""
            for r in rows[:cfg["scoring"]["top_n_strikes"]]:
                bg, fg = COLOR[r["color"]]
                comp = r["comps"]
                def mark(x):
                    return "✔" if x is True else ("—" if x is None else "✘")
                body += (
                    f"<tr style='background:{bg};color:{fg}'>"
                    f"<td><b>{int(r['strike'])}</b></td>"
                    f"<td>{r['delta']}</td>"
                    f"<td>{int(r['oi']) if r['oi'] is not None else '—'}</td>"
                    f"<td>{int(r['oi_change']) if r['oi_change'] is not None else '—'}</td>"
                    f"<td>{r['iv']}</td>"
                    f"<td>{('+' if r['iv_chg'] and r['iv_chg']>0 else '')}{r['iv_chg'] if r['iv_chg'] is not None else '—'}</td>"
                    f"<td>{r['trail']}</td>"
                    f"<td>{mark(comp['delta_band'])}</td><td>{mark(comp['oi_rise'])}</td>"
                    f"<td>{mark(comp['iv_flat'])}</td><td>{mark(comp['vol_passive'])}</td><td>{mark(comp['support'])}</td>"
                    f"<td><b>{r['ntrue']}✔/{r['nna']}—</b></td>"
                    f"<td>{r['verdict']}</td></tr>")
            sec += (
                "<table class='t'><tr><th>行权价</th><th>Delta</th><th>OI</th><th>ΔOI</th><th>IV</th>"
                "<th>IVΔ</th><th>连升</th><th>①OTM带</th><th>②连升</th><th>③IV平</th>"
                "<th>④成交*</th><th>⑤支撑*</th><th>确认/缺</th><th>结论</th></tr>"
                + body + "</table>")

    return f"""<!doctype html><html lang='zh-CN'><head><meta charset='utf-8'>
<meta name='viewport' content='width=device-width,initial-scale=1'>
<title>CBOT 卖Put集中度面板 · {latest}</title>
<style>
 body{{font-family:-apple-system,'Segoe UI','Microsoft YaHei',sans-serif;background:#fff;color:#222;margin:0;padding:24px;}}
 h1{{font-size:22px;margin:0 0 4px}} h2{{font-size:18px;margin:24px 0 8px;border-left:4px solid #1a4f9c;padding-left:8px}}
 h3{{font-size:15px;margin:16px 0 6px;color:#444}}
 .meta{{color:#666;font-size:13px;margin-bottom:12px}}
 table.t{{border-collapse:collapse;width:100%;font-size:12.5px;margin-bottom:8px}}
 table.t th,table.t td{{border:1px solid #e2e2e2;padding:5px 7px;text-align:right}}
 table.t th{{background:#f6f8fb;color:#333;text-align:right}}
 table.t td:first-child,table.t th:first-child{{text-align:left}}
 .note{{background:#fbfbfb;border:1px solid #eee;border-radius:8px;padding:14px 16px;font-size:13px;line-height:1.7;color:#444}}
 .note b{{color:#c0392b}}
 code{{background:#f0f0f0;padding:1px 4px;border-radius:3px}}
</style></head><body>
{demo_banner}
<h1>CBOT 农产品 卖Put集中度面板</h1>
<div class='meta'>报告日 {latest} ｜ 数据：{fresh} ｜ 标的：ZS 大豆 / ZM 豆粕 / ZC 玉米 / ZL 豆油（各主力+次主力）</div>
{bands_html}
{sec}
<h2>③ 方法论与数据缺口说明</h2>
<div class='note'>
<b>本面板是"定位/筛选"工具，不是"判定"工具。</b> OI 由买卖双方共同生成，单看 OI 分不出多空；下列 5 步打分本质是"产业卖 put 的概率先验 + 行为确认"。
<ul>
<li><b>数据性质（务必看清）</b>：本面板的逐档 OI 来自<b>免费公开源（Barchart / CME QuikStrike Essentials）的延迟逐档【总】Open Interest</b>，<b>不含买卖方向标签</b>。因此"卖Put集中"是对"高虚值 Put 高 OI 堆"做的<b>概率推断，不是确定性事实</b>；免费源本身无法区分开仓的买方/卖方。这与付费终端（Wind/Bloomberg）看得到 OI 增减时序、期权 flow 有本质差距。</li>
<li><b>① OTM delta 带(0.10–0.25)</b>：卖权收租几乎都在虚值区，是有效的先验过滤；落在带外（近ATM）直接判定偏买Put。</li>
<li><b>② OI 连续上升≥2日</b>：基于多日 OI 历史判断"分批挂单"而非单日脉冲；免费快照无 OI 日增量，故连升信号靠<b>每日累积的历史快照</b>形成（运行数日后才稳定）。</li>
<li><b>③ IV 不跟涨</b>：卖压增加压抑隐波，是最扎实的行为信号之一；需前一日 IV 对比。</li>
<li><b>④ 成交结构（被动挂单 vs 大单扫单）*</b>：<b>免费 OI 导出无逐笔/大单字段，本面板标记为"—"（不可用）</b>；真要判定需 QuikStrike Block Trade Browser 或逐笔数据。</li>
<li><b>⑤ 价格支撑*</b>：日线只能近似（期货在行权价上方且不远）。真正的"试探不破"需日内证据，结论仅供参考。</li>
</ul>
<b>想更硬的证据</b>：可叠加 CFTC COT（商业/互换商是否净空期权）或付费终端的 OI 增减时序——但本面板当前未接入，需另行提供。
<br>⚠️ 一旦价格有效跌破支撑带，卖方被动止损易触发踩踏，支撑变压力。
</div>
</body></html>"""


def render_md(cfg, latest, results, cluster_report):
    pname = cfg["products"]
    demo_note = "⚠️ 本文为**演示数据（构造样张）**，非真实行情，仅供验证逻辑。\n\n" if cfg.get("demo") else ""
    lines = [f"# CBOT 卖Put集中度日报 · {latest}", "", demo_note]
    lines.append("## 卖Put支撑带总览")
    if cluster_report:
        lines.append("| 产品 | 合约 | 价位区间 | 档数 | 累计OI | 各档分 |")
        lines.append("|---|---|---|---|---|---|")
        for b in cluster_report:
            lines.append(f"| {pname[b['product']]['name']} | {b['label']} {b['expiry']} | {b['lo']}–{b['hi']} | {b['n']} | {int(b['oi']):,} | {b['scores']} |")
    else:
        lines.append("本日无满足高度/中度集中的支撑带。")
    lines.append("")
    for prod in cfg["products"]:
        lines.append(f"## {pname[prod]['name']}（{prod}）")
        for (exp, label), rows in results[prod].items():
            lines.append(f"### {label}合约 · {exp}（期货参考 {rows[0]['underlying'] if rows else '—'}）")
            top = [r for r in rows if r["color"] in ("blue", "teal")][:5]
            if not top:
                lines.append("- 无显著卖Put集中档。")
            for r in top:
                lines.append(f"- 行权价 **{int(r['strike'])}** | Delta {r['delta']} | OI {int(r['oi']) if r['oi'] else '—'} | 连升 {r['trail']}日 | 评分 {r['ntrue']}✔/{r['nna']}— → {r['verdict']}")
            lines.append("")
    return "\n".join(lines)


if __name__ == "__main__":
    import sys
    d = sys.argv[1] if len(sys.argv) > 1 else None
    run(d)
