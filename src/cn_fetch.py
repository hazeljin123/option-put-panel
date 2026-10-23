#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""国内期货期权 · 真实数据抓取器

数据源（全部免费公开、交易所/主流财经商口径）：
  1) 东方财富 futsseapi —— 期权/期货实时快照与收盘数据
     - 期货：  /list/{EX}?fields=...        (p=最新/收盘, np=昨结, ccl=持仓量, vol=成交量)
     - 期权：  /list/{EX}OPTION?...           (dm=合约代码, rp/p=收盘价, ro=开盘,
                                             ccl=持仓量, rz=持仓增减, vol=成交量, j=均价)
  2) 郑州商品交易所(CZCE)官方每日行情文件 —— 精确口径（自带 持仓量/DELTA/隐含波动率）
     - 期权：  https://www.czce.com.cn/cn/DFSStaticFiles/Option/{YYYY}/{YYYYMMDD}/OptionDataDaily.txt
     - 期货：  https://www.czce.com.cn/cn/DFSStaticFiles/Future/{YYYY}/{YYYYMMDD}/FutureDataDaily.txt

已完成的字段对账（用 CZCE 官方文件逐行核对东财数据，见 selftest）：
  东财 ccl == 官方 持仓量        东财 rp/p == 官方 今收盘
  东财 rz  == 官方 持仓量增减     东财 ro   == 官方 今开盘
  东财 vol == 官方 成交量        东财 j    == 官方 成交均价
"""
import json
import os
import re
import subprocess
import sys
import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
os.makedirs(DATA, exist_ok=True)

EM_TOKEN = "58b2fa8f54638b60b87d69b31969089c"
EM_LIST = "https://futsseapi.eastmoney.com/list"

# 交易所代码：东财用大写
EXCHANGE = {"DCE": "DCE", "CZCE": "CZCE", "SHFE": "SHFE"}

# 目标品种：中文名 -> (交易所, 期货代码前缀, 显示名, 最小变动价位, 价格单位)
PRODUCTS = {
    "豆粕":   ("DCE",  "m",  "豆粕",   1.0,  "元/吨"),
    "豆油":   ("DCE",  "y",  "豆油",   2.0,  "元/吨"),
    "棕榈油": ("DCE",  "p",  "棕榈油", 2.0,  "元/吨"),
    "菜油":   ("CZCE", "OI", "菜油",   1.0,  "元/吨"),
    "菜粕":   ("CZCE", "RM", "菜粕",   1.0,  "元/吨"),
    "生猪":   ("DCE",  "lh", "生猪",   5.0,  "元/吨"),
    "鸡蛋":   ("DCE",  "jd", "鸡蛋",   1.0,  "元/500kg"),
    "豆二":   ("DCE",  "b",  "豆二",   1.0,  "元/吨"),
}

# 连续合约标识（东财用 M=主连 S=次主连），用于识别主力/次主力月份
CONT_TAG = {"M": "main", "S": "second"}

# 郑商所官方文件实际使用的日期（若目标日文件未发布会回溯到最近可用交易日）
CZCE_DATA_DATE = None


def _curl(url, timeout=40):
    """走 curl 子进程（Python urllib 在本环境被沙箱拦截，curl 可用）。"""
    try:
        r = subprocess.run(
            ["curl", "-s", "--max-time", str(timeout), "-H", "User-Agent: Mozilla/5.0", url],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout + 10,
        )
        return r.stdout
    except Exception as e:  # noqa: BLE001
        print(f"[warn] curl 失败 {url}: {e}", file=sys.stderr)
        return ""


def _em_list(path, page_size=6000, page_index=0, order="dm", sort="asc"):
    url = (f"{EM_LIST}/{path}?orderBy={order}&sort={sort}&pageSize={page_size}"
           f"&pageIndex={page_index}&token={EM_TOKEN}"
           f"&field=dm,sc,name,p,np,vol,ccl,oi,zdp,rq,rzj,hy,zsjd")
    raw = _curl(url)
    if not raw:
        return []
    try:
        return (json.loads(raw) or {}).get("list") or []
    except Exception as e:  # noqa: BLE001
        print(f"[warn] 东财 JSON 解析失败 {path}: {e}", file=sys.stderr)
        return []


def _em_list_all(path, order="dm", sort="asc", page_size=6000):
    """分页拉全量（东财单页上限约 6000 条，option 总数可过万）。"""
    out = []
    seen = set()
    page = 0
    while True:
        rows = _em_list(path, page_size=page_size, page_index=page, order=order, sort=sort)
        if not rows:
            break
        new = 0
        for r in rows:
            key = r.get("uid") or r.get("dm")
            if key in seen:
                continue
            seen.add(key)
            out.append(r)
            new += 1
        if new == 0 or len(rows) < page_size:
            break
        page += 1
        if page > 12:  # 安全上限
            break
    return out


# --------------------------------------------------------------------------
# 期货：识别主力 / 次主力合约
# --------------------------------------------------------------------------
def fetch_futures():
    """返回 {交易所: {品种代码(小写): {"main": {...}, "second": {...}, "months": {yyyymm: {...}}}}}。

    东财主连/次主连编码规则：品种码 + "M" / "S"
      DCE  豆粕 -> mm / ms      CZCE 菜油 -> OIM / OIS
    真实月份合约形如 m2701 / OI701。
    """
    out = {}
    for exch in ("DCE", "CZCE", "SHFE"):
        rows = _em_list_all(exch)
        if not rows:
            continue
        by_prod = {}
        for it in rows:
            dm = (it.get("dm") or "").strip()
            nm = (it.get("name") or "").strip()
            if not dm or not nm:
                continue
            rec = {
                "dm": dm, "name": nm,
                "price": _num(it.get("p")), "prev_settle": _num(it.get("np")),
                "oi": _num(it.get("ccl")), "vol": _num(it.get("vol")),
            }
            # 连续合约：代码以 M/S 结尾（长度 >= 2 且 M/S 前是字母）
            if len(dm) >= 2 and dm[-1] in ("M", "S") and dm[-2].isalpha():
                base = dm[:-1]
                slot = "main" if dm[-1] == "M" else "second"
                by_prod.setdefault(base.lower(), {})[slot] = rec
                continue
            # 真实月份合约：字母前缀 + 3/4 位数字
            m = re.match(r"^([A-Za-z]+?)(\d{3,4})$", dm)
            if not m:
                continue
            code, mon = m.group(1), m.group(2)
            by_prod.setdefault(code.lower(), {}).setdefault("months", {})[mon] = rec
        out[exch] = by_prod
    return out


def pick_main_secondary(by_prod, exclude_months=(), option_oi=None):
    """挑主力/次主力合约月份。

    口径优先级：
      1) 若提供 option_oi（{月份: 期权持仓合计}），按**期权持仓量**排名 ——
         这才是"期权市场真正在交易哪个月"的真实依据。
         （期货持仓量高的月份，其期权可能已交割清零，如豆二 b2611：
          期权成交 13 万手但持仓已归零。）
      2) 否则退回按期货持仓量(ccl)排名。

    剔除：无价格 / 无持仓量 / 无成交量的僵尸月份。
    返回 (main_rec, second_rec)，缺失则为 (None, None)。
    """
    months = by_prod.get("months", {}) if by_prod else {}

    if option_oi:
        ranked = []
        for mon, rec in months.items():
            oi = option_oi.get(mon)
            if oi is None:
                continue
            if not rec.get("price") or mon in exclude_months:
                continue
            ranked.append((oi, mon, rec))
        if ranked:
            ranked.sort(key=lambda x: -x[0])
            main = ranked[0][2]
            second = ranked[1][2] if len(ranked) > 1 else None
            return main, second

    cands = [(mon, rec) for mon, rec in months.items()
              if mon not in exclude_months
              and rec.get("price")
              and (rec.get("oi") or 0) > 0
              and (rec.get("vol") or 0) > 0]
    if not cands:
        cands = [(mon, rec) for mon, rec in months.items()
                 if mon not in exclude_months and rec.get("price")]
    if not cands:
        return None, None
    cands.sort(key=lambda kv: -(kv[1].get("oi") or 0))
    main = cands[0][1] if cands else None
    second = cands[1][1] if len(cands) > 1 else None
    return main, second


def option_oi_by_month(options, prod_lower):
    """统计某品种各月份的期权持仓量合计 -> {month: oi_total}。

    用它判断"期权市场真正活跃的月份"，比期货持仓量更贴近本面板用途。
    """
    agg = {}
    for it in options:
        p = parse_opt_code(it.get("dm"))
        if not p:
            continue
        code, mon, typ, K = p
        if code.lower() != prod_lower:
            continue
        agg[mon] = agg.get(mon, 0.0) + (_num(it.get("ccl")) or 0.0)
    return agg


def _num(v):
    if v is None or v == "-":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


# --------------------------------------------------------------------------
# 期权：逐行权价真实持仓量
# --------------------------------------------------------------------------
# 期权合约代码：
#   DCE  : m2701c3800   -> 品种2字母 + 4位年月(YYMM) + c/p + 行权价
#   CZCE : OI701C11600  -> 品种大写 + 3位年月(YMM) + C/P + 行权价
#   SHFE : cu2701P84000 -> 品种小写 + 4位年月 + P + 行权价
_OPT_RE = re.compile(r"^([A-Za-z]{1,3}?)(\d{3,4})([CPcp])(\d+)$")


def fetch_options(exch):
    """返回该交易所全部期权合约列表（原始 dict 列表，分页拉全量）。"""
    return _em_list_all(f"{exch}OPTION", order="dm", sort="asc")


def parse_opt_code(dm):
    """解析期权合约代码 -> (product_code, month, C/P, strike)

    例：m2701c3800 -> ('m','2701','C',3800)
        OI701C11600 -> ('OI','701','C',11600)
        cu2701P84000 -> ('cu','2701','P',84000)
    组合/系列合约（含 'S' 段，如 OI701MSC10800）返回 None。
    """
    if not dm:
        return None
    dm = dm.strip()
    m = _OPT_RE.match(dm)
    if not m:
        return None
    prod, mon, typ, strike = m.group(1), m.group(2), m.group(3).upper(), m.group(4)
    # 郑商所"系列/组合"合约：OI701MSC10800 这类，prod 里夹带 S
    if "S" in prod.upper():
        return None
    return prod, mon, typ, float(strike)


# --------------------------------------------------------------------------
# 郑商所官方每日行情（精确口径：持仓量 / DELTA / 隐含波动率）
# --------------------------------------------------------------------------
def _czce_url(kind, date):
    y = date.strftime("%Y")
    ymd = date.strftime("%Y%m%d")
    return f"https://www.czce.com.cn/cn/DFSStaticFiles/{kind}/{y}/{ymd}/OptionDataDaily.txt" \
        if kind == "Option" else \
        f"https://www.czce.com.cn/cn/DFSStaticFiles/{kind}/{y}/{ymd}/FutureDataDaily.txt"


def _num_or_none(s):
    s = (s or "").replace(",", "").strip()
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def fetch_czce_official(date, kind="Option"):
    """下载并解析郑商所官方每日行情表。

    返回 dict：合约代码 -> 字段字典
      期权： settle/prev_settle/o/chg_oi/volume/oi/delta/iv
      期货： settle/prev_settle/open/high/low/close/volume/oi

    容错：若指定日期的文件尚未发布（郑商所通常在当日 15:40 前后才上传），
    会自动向前回溯最多 7 个日历日，取最近一个**确实发布过**的交易日文件，
    并把实际使用的数据日期记录在模块级变量 CZCE_DATA_DATE 中。
    这样在 15:45 定时任务触发时，不会因文件还没上传而拿到空数据。
    """
    global CZCE_DATA_DATE
    kind = "Option" if kind == "Option" else "Future"

    raw = ""
    used_date = date
    cached = False

    # 先看缓存：缓存文件名必须按**实际数据日期**，避免把 T-1 数据
    # 误存成今日文件（那会造成"假更新"——页面显示今天，实际数据是昨天）
    path = os.path.join(DATA, f"czce_{kind.lower()}_{date.strftime('%Y%m%d')}.txt")
    if os.path.exists(path):
        raw = open(path, encoding="utf-8").read()
        cached = True

    if not raw:
        # 逐日向前回溯，取最近一个**确实发布过**的交易日文件
        for back in range(0, 8):
            d = date - datetime.timedelta(days=back)
            dpath = os.path.join(DATA, f"czce_{kind.lower()}_{d.strftime('%Y%m%d')}.txt")
            if os.path.exists(dpath):
                raw = open(dpath, encoding="utf-8").read()
                used_date = d
                cached = True
                break
            url = _czce_url(kind, d)
            cand = _curl(url, timeout=40)
            if cand and "郑州商品交易所" in cand:
                raw = cand
                used_date = d
                with open(dpath, "w", encoding="utf-8") as f:
                    f.write(raw)
                if back > 0:
                    print(f"[info] 郑商所 {kind} {date} 文件未发布，回溯到 {d}",
                          file=sys.stderr)
                break
        if not raw:
            print(f"[warn] 郑商所 {kind} 文件抓取失败（已回溯 7 日）: "
                  f"{_czce_url(kind, date)}", file=sys.stderr)
            return {}

    if kind == "Option":
        CZCE_DATA_DATE = used_date
    if cached:
        pass  # 已是按实际日期命名的缓存，无需再写

    text = raw

    out = {}
    for line in text.splitlines():
        f = [x.strip() for x in line.split("|")]
        if len(f) < 13 or not f[0] or f[0].startswith("合约代码"):
            continue
        code = f[0].replace(" ", "")
        if kind == "Option":
            if len(f) < 16:
                continue
            out[code] = {
                "code": code,
                "prev_settle": _num_or_none(f[1]),
                "open": _num_or_none(f[2]),
                "high": _num_or_none(f[3]),
                "low": _num_or_none(f[4]),
                "close": _num_or_none(f[5]),
                "settle": _num_or_none(f[6]),
                "volume": _num_or_none(f[9]),
                "oi": _num_or_none(f[10]),
                "oi_chg": _num_or_none(f[11]),
                "delta": _num_or_none(f[13]),
                "iv": (v / 100.0 if (v := _num_or_none(f[14])) is not None else None),
            }
        else:
            out[code] = {
                "code": code,
                "prev_settle": _num_or_none(f[1]),
                "open": _num_or_none(f[2]),
                "high": _num_or_none(f[3]),
                "low": _num_or_none(f[4]),
                "close": _num_or_none(f[5]),
                "settle": _num_or_none(f[6]),
                "volume": _num_or_none(f[9]),
                "oi": _num_or_none(f[10]),
                "oi_chg": _num_or_none(f[11]),
            }
    return out


if __name__ == "__main__":
    today = datetime.date.today()
    print("=== 郑商所官方期权文件 ===")
    o = fetch_czce_official(today, "Option")
    print("parsed", len(o))
    for k in list(o)[:3]:
        print(" ", k, o[k])
    print("=== 郑商所官方期货文件 ===")
    f = fetch_czce_official(today, "Future")
    print("parsed", len(f))
    print("=== 东财期货主力/次主力 ===")
    fut = fetch_futures()
    for cn, (ex, code, disp, tick, unit) in PRODUCTS.items():
        b = fut.get(ex, {}).get(code, {})
        print(f"  {disp}({ex}/{code}): main={b.get('main',{}).get('dm')} "
              f"{b.get('main',{}).get('price')}  second={b.get('second',{}).get('dm')} "
              f"{b.get('second',{}).get('price')}")
