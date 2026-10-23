#!/usr/bin/env python3
# 从新浪财经获取 CBOT 农产品（大豆/豆粕/玉米/豆油）真实连续主力期货价。
# 免费、公开、无需账号。代码格式为 hf_<code>（无数字后缀），返回 GBK 编码。
# 注意：新浪连续主力(hf_S等)是"主连"合约价，代表近月主力；具体到 NOV/JAN 等
# 合约月份的价需用 QuikStrike/CME 或新浪特定月份代码，本脚本先用主连价推算理论带。
import urllib.request, json, re, os, datetime, sys

SINA = {
    "ZS": ("hf_S",  "美国大豆", "cents/bu"),
    "ZM": ("hf_SM", "美黄豆粉", "USD/ton"),
    "ZC": ("hf_C",  "美国玉米", "cents/bu"),
    "ZL": ("hf_BO", "美黄豆油", "cents/lb"),
}
REF = "https://finance.sina.com.cn/"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"


def fetch():
    codes = ",".join(v[0] for v in SINA.values())
    # 新浪 hq.sinajs.cn 的 https 握手在部分网络环境不稳定（curl rc=0 但内容为空），
    # http 反而稳定返回；同时给出多个备选主机，任一成功即可。
    hosts = ["http://hq.sinajs.cn", "https://hq.sinajs.cn",
             "http://hq.sinajs.cn.cn", "https://hq.sinajs.cn.cn"]
    raw = None
    last_err = ""
    for host in hosts:
        url = f"{host}/list={codes}"
        for attempt in range(3):        # 每主机重试 3 次，间隔递增
            try:
                import subprocess
                out = subprocess.run(
                    ["curl", "-s", "--max-time", "25", "-A", UA,
                     "-H", f"Referer: {REF}", url],
                    capture_output=True, timeout=35)
                body = out.stdout
                if body and b"hq_str_hf_" in body:
                    raw = body
                    break
                last_err = f"{host} 返回空内容"
            except Exception as e:  # noqa: BLE001
                last_err = f"{host}: {e}"
            if attempt < 2:
                import time
                time.sleep(1.5 * (attempt + 1))
        if raw:
            break
    if raw is None:
        print(f"[warn] 新浪抓取失败：{last_err}", file=sys.stderr)
    if raw is None:
        try:
            req = urllib.request.Request(
                f"https://hq.sinajs.cn/list={codes}",
                headers={"User-Agent": UA, "Referer": REF})
            raw = urllib.request.urlopen(req, timeout=25).read()
        except Exception:
            raw = b""
    if not raw:
        return {}
    try:
        text = raw.decode("gbk")
    except Exception:
        text = raw.decode("utf-8", "ignore")
    prices = {}
    for line in text.split(";"):
        m = re.search(r'hq_str_(hf_[A-Za-z0-9]+)="([^"]*)"', line)
        if not m:
            continue
        code, body = m.group(1), m.group(2)
        if not body:
            continue
        prod = next((k for k, v in SINA.items() if v[0] == code), None)
        if not prod:
            continue
        f = body.split(",")
        try:
            price = float(f[0])
            date = f[12] if len(f) > 12 else ""
            name = f[13] if len(f) > 13 else SINA[prod][1]
        except Exception:
            continue
        prices[prod] = {"code": code, "name": name, "price": price,
                        "unit": SINA[prod][2], "date": date}
    return prices


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    out_dir = os.path.join(here, "data", "realtime")
    os.makedirs(out_dir, exist_ok=True)
    try:
        prices = fetch()
    except Exception as e:
        print(f"ERROR: 新浪抓取失败: {e}", file=sys.stderr)
        sys.exit(2)
    if not prices:
        print("ERROR: 新浪返回空（网络被挡或代码失效）", file=sys.stderr)
        sys.exit(1)
    payload = {"updated": datetime.date.today().isoformat(), "prices": prices}
    with open(os.path.join(out_dir, "sina_prices.json"), "w", encoding="utf-8") as fp:
        json.dump(payload, fp, ensure_ascii=False, indent=2)
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
