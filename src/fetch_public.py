#!/usr/bin/env python3
# 公开源抓取器（best-effort）。目标：Barchart 免费期权链 CSV（含 Delta/IV/OI/成交量）。
# 说明：本执行环境对 CME(502)/Barchart(202反爬) 直连被挡；此脚本应在"能正常出网、
# 且未被反爬拦截"的机器上运行（如你本机）。任何失败都优雅退出，交由投喂数据兜底。
import csv, io, os, sys, datetime

BASE = os.path.dirname(os.path.abspath(__file__))
INCOMING = os.path.join(BASE, "data", "incoming")

# Barchart 期权符号：*0=主力(lead) *1=次主力(next)
LEAD = {"ZS": "ZS*0", "ZM": "ZM*0", "ZC": "ZC*0", "ZL": "ZL*0"}
NEXT = {"ZS": "ZS*1", "ZM": "ZM*1", "ZC": "ZC*1", "ZL": "ZL*1"}

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120 Safari/537.36")

CANON = ["date", "product", "expiry", "strike", "type", "oi", "oi_change",
         "delta", "iv", "volume", "underlying"]


def _fetch_csv(sym):
    try:
        import urllib.request
        url = (f"https://www.barchart.com/futures/quotes/{sym}/options/"
               f"free-download?fileName={sym}&extension=csv")
        req = urllib.request.Request(url, headers={"User-Agent": UA,
                                                   "Accept": "text/csv,*/*"})
        with urllib.request.urlopen(req, timeout=30) as r:
            data = r.read().decode("utf-8", "replace")
        if len(data) < 200:
            return None
        return data
    except Exception as e:
        print(f"  [fetch] {sym} 失败: {e}", file=sys.stderr)
        return None


def _col(row, *names):
    low = {k.lower(): v for k, v in row.items()}
    for n in names:
        if n.lower() in low and low[n.lower()] not in ("", None):
            return low[n.lower()]
    return None


def _num(v):
    try:
        return float(str(v).replace(",", "").replace("%", ""))
    except (TypeError, ValueError):
        return None


def parse_barchart(product, sym, text, date):
    out = []
    rd = csv.DictReader(io.StringIO(text))
    for row in rd:
        typ = (_col(row, "Type") or "").lower()
        if typ not in ("put", "p"):
            continue
        strike = _num(_col(row, "Strike"))
        oi = _num(_col(row, "Open Interest"))
        if strike is None or oi is None:
            continue
        out.append({
            "date": date, "product": product, "expiry": sym,
            "strike": int(strike) if strike == int(strike) else strike,
            "type": "P",
            "oi": int(oi),
            "oi_change": _num(_col(row, "Open Interest Change", "OI Change")),
            "delta": _num(_col(row, "Delta")),
            "iv": _num(_col(row, "Implied Volatility", "IV")),
            "volume": _num(_col(row, "Volume")),
            "underlying": _num(_col(row, "Underlying Price", "Underlying")),
        })
    return out


def main():
    date = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8)))
    date = date.strftime("%Y-%m-%d")
    total = 0
    for prod in LEAD:
        for label, sym in (("主力", LEAD[prod]), ("次主力", NEXT[prod])):
            text = _fetch_csv(sym)
            if not text:
                continue
            rows = parse_barchart(prod, sym, text, date)
            if not rows:
                continue
            path = os.path.join(INCOMING, f"cbot_options_{date}_{prod}_{label}.csv")
            with open(path, "w", newline="", encoding="utf-8") as f:
                w = csv.DictWriter(f, fieldnames=CANON)
                w.writeheader()
                for r in rows:
                    w.writerow(r)
            print(f"  [ok] {prod} {label} {sym}: {len(rows)} 档put -> {path}")
            total += len(rows)
    print(f"抓取完成，共 {total} 档put写入 incoming/（若0说明本环境被挡，请用QuikStrike导出投喂）")
    return total


if __name__ == "__main__":
    main()
