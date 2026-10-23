#!/usr/bin/env python3
# CFTC COT 公开免费源（disaggregated 分类报告，年度 CSV）
# 这是 CFTC 官方公开、免费、无需账号的数据：
#   https://www.cftc.gov/files/dea/history/com_disagg_txt_<YEAR>.zip
# 解压后 c_year.txt 是 CSV，含每个市场×每周的：
#   商业户(Prod_Merc) / 互换商(Swap) / 管理基金(M_Money) 的 多/空/价差 持仓
#   以及 FutOnly_or_Combined 标记（Combined = 期货+期权合计）
# 本脚本提取 ZS/ZM/ZC/ZL 最新一期的「期货+期权合并」持仓，
# 并拆出「期权-only = Combined - FutOnly」作为卖put(期权空头)的方向证据。
import csv, io, os, subprocess, zipfile, datetime, sys, json

HERE = os.path.dirname(os.path.abspath(__file__))
HIST = os.path.join(HERE, "data", "history")
os.makedirs(HIST, exist_ok=True)

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"

# CBOT 农产品市场名关键字（交易所固定为 CHICAGO BOARD OF TRADE）
MARKETS = {
    "ZS": "SOYBEANS - CHICAGO BOARD OF TRADE",
    "ZM": "SOYBEAN MEAL - CHICAGO BOARD OF TRADE",
    "ZL": "SOYBEAN OIL - CHICAGO BOARD OF TRADE",
    "ZC": "CORN - CHICAGO BOARD OF TRADE",
}
NAME_CN = {"ZS": "CBOT大豆", "ZM": "CBOT豆粕", "ZC": "CBOT玉米", "ZL": "CBOT豆油"}


def ensure_txt(year):
    txt_path = os.path.join(HIST, f"cot_disagg_{year}.txt")
    need = True
    if os.path.exists(txt_path):
        mtime = datetime.datetime.fromtimestamp(os.path.getmtime(txt_path))
        if (datetime.datetime.now() - mtime).days < 6:
            need = False
    if need:
        zpath = os.path.join(HIST, f"cot_disagg_{year}.zip")
        url = f"https://www.cftc.gov/files/dea/history/com_disagg_txt_{year}.zip"
        rc = subprocess.run(
            ["curl", "-sL", "--max-time", "90", "-A", UA, url, "-o", zpath],
            capture_output=True, timeout=120,
        )
        if rc.returncode != 0 or not os.path.exists(zpath) or os.path.getsize(zpath) < 100000:
            sys.stderr.write(f"COT 下载失败 rc={rc.returncode}\n")
            return None
        with zipfile.ZipFile(zpath) as z:
            nm = [n for n in z.namelist() if n.endswith(".txt")]
            if not nm:
                return None
            data = z.read(nm[0])
        with open(txt_path, "wb") as f:
            f.write(data)
        try:
            os.remove(zpath)
        except Exception:
            pass
    return txt_path


def load_year(year):
    txt = ensure_txt(year)
    if not txt or not os.path.exists(txt):
        return None
    with open(txt, "r", encoding="utf-8", errors="ignore", newline="") as f:
        reader = csv.DictReader(f)
        return list(reader)


def _num(row, col):
    v = (row.get(col) or "").strip().replace(",", "")
    try:
        return int(float(v))
    except Exception:
        return 0


def extract():
    """返回 {prod: {report_date, oi, prod_net, swap_net, mm_net,
       opt_prod_net, opt_swap_net, opt_mm_net, prod_net_pct}}"""
    year = datetime.date.today().year
    rows = load_year(year)
    if not rows:
        # 退一年试（跨年首周数据可能在去年文件）
        rows = load_year(year - 1)
    if not rows:
        return None
    # 按市场归集：取最新 report_date 的 Combined 与 FutOnly 行
    latest = {}
    for r in rows:
        mkt = r.get("Market_and_Exchange_Names", "")
        prod = None
        for k, kw in MARKETS.items():
            if mkt.strip().upper().startswith(kw):
                prod = k
                break
        if not prod:
            continue
        if r.get("FutOnly_or_Combined", "").strip() not in ("Combined", "FutOnly"):
            continue
        d = r.get("Report_Date_as_YYYY-MM-DD", "")
        cur = latest.setdefault(prod, {})
        if d > cur.get("date", ""):
            cur["date"] = d
            cur[r["FutOnly_or_Combined"].strip()] = r
    out = {}
    for prod, rec in latest.items():
        c = rec.get("Combined")
        fo = rec.get("FutOnly")
        if not c:
            continue
        oi = _num(c, "Open_Interest_All")
        p_net = _num(c, "Prod_Merc_Positions_Long_All") - _num(c, "Prod_Merc_Positions_Short_All")
        s_net = _num(c, "Swap_Positions_Long_All") - _num(c, "Swap__Positions_Short_All")
        m_net = _num(c, "M_Money_Positions_Long_All") - _num(c, "M_Money_Positions_Short_All")
        p_net_pct = (p_net / oi * 100) if oi else 0.0
        # 期权-only = Combined - FutOnly
        opt = {}
        if fo:
            opt["p_net"] = (p_net
                            - (_num(fo, "Prod_Merc_Positions_Long_All") - _num(fo, "Prod_Merc_Positions_Short_All")))
            opt["s_net"] = (s_net
                            - (_num(fo, "Swap_Positions_Long_All") - _num(fo, "Swap__Positions_Short_All")))
            opt["m_net"] = (m_net
                            - (_num(fo, "M_Money_Positions_Long_All") - _num(fo, "M_Money_Positions_Short_All")))
        else:
            opt = {"p_net": None, "s_net": None, "m_net": None}
        out[prod] = {
            "report_date": rec["date"], "oi": oi,
            "prod_net": p_net, "swap_net": s_net, "mm_net": m_net,
            "prod_net_pct": round(p_net_pct, 1),
            "opt_prod_net": opt["p_net"], "opt_swap_net": opt["s_net"], "opt_mm_net": opt["m_net"],
        }
    return out


if __name__ == "__main__":
    res = extract()
    if not res:
        print("COT 提取失败", file=sys.stderr)
        raise SystemExit(2)
    print(json.dumps(res, ensure_ascii=False, indent=2))
