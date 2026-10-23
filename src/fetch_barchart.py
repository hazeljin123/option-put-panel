#!/usr/bin/env python3
# fetch_barchart.py
# 目标：复制豆包所用的「免费公开逐档总 OI」数据层 —— 从 Barchart 免费期权页拉 ZS/ZM/ZC/ZL
#       各到期月的「逐档 Put 总 OI / ΔOI(无) / Delta / IV / Volume」，对齐 pipeline.py 的输入格式。
#
# 重要诚实声明（与豆包路线完全一致）：
#   - Barchart 免费源给的是「延迟、收盘静态」的【逐档总 Put OI】，【不分买卖方向】；
#   - 因此本脚本产出的「卖Put集中」是「对高虚值 Put 高 OI 堆做的概率推断」，不是确定性事实；
#   - 免费源没有 OI 逐日增量（Change 是期权本身价格变动，非 OI 变动），
#     所以 Step2「OI 连续上升」依赖【多日累积的历史快照】——每天跑一次，几天后才有连升信号。
#
# 环境：本执行沙箱对 Barchart 返回 202 反爬、对 CME 返回 000，故脚本在沙箱会优雅失败并 dump 原始响应；
#       在你本机（正常出网、干净 IP）直接 `python fetch_barchart.py` 即可拉到真实数据。
import csv, io, os, sys, subprocess, datetime, re

BASE = os.path.dirname(os.path.abspath(__file__))
INCOMING = os.path.join(BASE, "data", "incoming")
HISTORY = os.path.join(BASE, "data", "history")
os.makedirs(INCOMING, exist_ok=True)
os.makedirs(HISTORY, exist_ok=True)

# Barchart 期权页符号（*0 = 全部到期月的前端序列）
BAR_PRODUCTS = {"ZS": "ZS*0", "ZM": "ZM*0", "ZC": "ZC*0", "ZL": "ZL*0"}
# 真实期货价来源（新浪，已在沙箱/本机验证可连）
try:
    from fetch_sina import fetch as fetch_sina_prices
except Exception:
    fetch_sina_prices = None

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")


def _curl_get(url, referer="https://www.barchart.com/"):
    try:
        out = subprocess.run(
            ["curl", "-sL", "--compressed", "--max-time", "40", "-A", UA,
             "-H", f"Referer: {referer}", url],
            capture_output=True, timeout=60)
        if out.returncode == 0 and out.stdout:
            return out.stdout, None
        return None, f"curl rc={out.returncode}"
    except Exception as e:
        return None, str(e)


def download_bar_csv(product):
    """尝试下载 Barchart options free-download CSV，返回 (text, err)。"""
    sym = BAR_PRODUCTS[product]
    url = (f"https://www.barchart.com/futures/quotes/{sym}/options"
           f"/free-download?fileName={sym}&extension=csv")
    return _curl_get(url)


# Barchart 期权 CSV 常见表头 -> 我们的语义字段。下载后按表头实际出现自适应匹配。
HDR_MAP = {
    "strike": ["strike", "Strike Price", "Strike"],
    "type": ["Type", "Put/Call", "PC", "Right"],
    "oi": ["Open Interest", "OpenInterest", "OI"],
    "delta": ["Delta"],
    "iv": ["Implied Volatility", "Implied Vol", "IV"],
    "volume": ["Volume", "Vol"],
    "contract": ["Contract", "Expiration", "Expiry", "Month"],
    "symbol": ["Symbol"],
}


def _hdr_index(header, candidates):
    for c in candidates:
        for i, h in enumerate(header):
            if h.strip().lower() == c.lower():
                return i
    return None


def _parse_bar_csv(text, product):
    """解析 Barchart 期权 CSV -> list of normalized dict（仅 Put）。失败返回 ([], reason)。"""
    try:
        # 兼容有 BOM / 不同换行
        raw = text.decode("utf-8-sig") if isinstance(text, bytes) else text
    except Exception:
        try:
            raw = text.decode("gbk", "ignore")
        except Exception:
            raw = str(text)
    lines = [ln for ln in raw.splitlines() if ln.strip()]
    if not lines:
        return [], "空响应"
    # 找表头行（含 Strike 或 Symbol）
    hdr_idx = None
    for i, ln in enumerate(lines):
        low = ln.lower()
        if "strike" in low or ("symbol" in low and "open interest" in low):
            hdr_idx = i
            break
    if hdr_idx is None:
        return [], "未找到期权表头（疑似非数据响应）"
    header = [c.strip() for c in lines[hdr_idx].split(",")]
    idx = {k: _hdr_index(header, v) for k, v in HDR_MAP.items()}
    if idx["strike"] is None or idx["oi"] is None:
        return [], f"表头缺少 Strike/OI，实际表头={header[:8]}"
    rows = []
    for ln in lines[hdr_idx + 1:]:
        f = ln.split(",")
        if len(f) < len(header):
            continue
        def g(k):
            i = idx[k]
            return f[i] if i is not None and i < len(f) else ""
        strike_s = g("strike").strip()
        if not re.match(r"^-?\d+(\.\d+)?$", strike_s):
            continue
        # 判定 Put / Call
        typ = g("type").strip().upper()
        if not typ:
            sym = g("symbol").upper()
            typ = "P" if sym.endswith("P") or " P" in sym or "PUT" in sym else ("C" if "C" in sym or "CALL" in sym else "")
        if typ not in ("P", "C"):
            continue
        # 到期月标签：从 Contract(如 ZSF27) 或 symbol 提取
        exp = g("contract").strip()
        if not exp:
            sym = g("symbol").upper()
            m = re.search(r"([FGHJKMNQUVXZ]\d{2})", sym)
            exp = m.group(1) if m else "?"
        def num(x):
            try:
                return float(x.replace("%", "").replace(",", ""))
            except Exception:
                return None
        rows.append({
            "product": product,
            "expiry": exp,
            "strike": float(strike_s),
            "type": typ,
            "oi": num(g("oi")),
            "delta": num(g("delta")) if idx["delta"] is not None else None,
            "iv": num(g("iv")) if idx["iv"] is not None else None,
            "volume": num(g("volume")) if idx["volume"] is not None else None,
        })
    return rows, None


def fetch(date=None):
    today = date or datetime.date.today().strftime("%Y-%m-%d")
    # 真实期货价（新浪）
    underlying = {}
    if fetch_sina_prices:
        try:
            underlying = fetch_sina_prices()
        except Exception as e:
            sys.stderr.write(f"新浪期货价获取失败（不影响 OI 抓取）: {e}\n")
    all_rows = []
    ok_products = []
    for prod in BAR_PRODUCTS:
        text, err = download_bar_csv(prod)
        if err or not text:
            sys.stderr.write(f"[{prod}] 下载失败: {err}\n")
            # dump 原始响应便于校准
            dbg = os.path.join(INCOMING, f"_barchart_raw_{prod}_{today}.txt")
            try:
                open(dbg, "wb").write(text or b"(empty)")
                sys.stderr.write(f"  原始响应已存 {dbg}（请在能出网的本机运行或发我校准）\n")
            except Exception:
                pass
            continue
        parsed, perr = _parse_bar_csv(text, prod)
        if perr:
            sys.stderr.write(f"[{prod}] 解析失败: {perr}\n")
            dbg = os.path.join(INCOMING, f"_barchart_raw_{prod}_{today}.txt")
            try:
                open(dbg, "wb").write(text if isinstance(text, bytes) else text.encode("utf-8", "ignore"))
                sys.stderr.write(f"  原始响应已存 {dbg}\n")
            except Exception:
                pass
            continue
        for r in parsed:
            r["underlying"] = underlying.get(prod, {}).get("price")
            r["date"] = today
            all_rows.append(r)
        ok_products.append(prod)
        sys.stderr.write(f"[{prod}] 解析成功：{len(parsed)} 档（Put+Call 合计）\n")

    if not all_rows:
        print("WARN: 全部产品下载/解析失败（本环境 Barchart 被反爬/代理挡，或本机未出网）。"
              "请在能正常出网的本机运行本脚本；或把 QuikStrike/Barchart 导出 CSV 直接放入 data/incoming/。"
              "未写入任何数据文件，避免产生空/假数据。")
        return None
    out_csv = os.path.join(INCOMING, f"cbot_options_{today}.csv")
    with open(out_csv, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["Date", "Product", "Expiry", "Strike", "Type",
                    "Open Interest", "Change", "Delta", "Implied Vol",
                    "Volume", "Underlying"])
        for r in all_rows:
            w.writerow([r["date"], r["product"], r["expiry"], r["strike"], r["type"],
                        r["oi"] if r["oi"] is not None else "",
                        "",  # 免费快照无 OI 日增量；连升信号靠多日累积
                        r["delta"] if r["delta"] is not None else "",
                        r["iv"] if r["iv"] is not None else "",
                        r["volume"] if r["volume"] is not None else "",
                        r["underlying"] if r["underlying"] is not None else ""])
    print(f"OK products={ok_products} total_rows={len(all_rows)} -> {out_csv}")
    return out_csv


if __name__ == "__main__":
    d = sys.argv[1] if len(sys.argv) > 1 else None
    fetch(d)
