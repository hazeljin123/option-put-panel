#!/usr/bin/env python3
# 生成 3 个交易日的样张数据，模拟 QuikStrike / CME VOI 逐档 Put OI 导出。
# 每个产品含：一个典型"卖Put聚簇"（OTM、OI连续升、IV略降）+ 一个"买Put异常"（近ATM、单日暴增、IV跳升）。
import csv, os

BASE = os.path.dirname(os.path.abspath(__file__))
INCOMING = os.path.join(BASE, "data", "incoming")
os.makedirs(INCOMING, exist_ok=True)

DATES = ["2026-10-21", "2026-10-22", "2026-10-23"]

# 每产品: underlying(3日), expiries, 各 put strike 档(含聚类/正常/异常)
PRODUCTS = {
    "ZS": {
        "underlying": [1175, 1178, 1180],
        "expiries": ["NOV26", "JAN27"],
        "strikes": [
            # 卖Put聚簇
            {"strike": 1080, "delta": 0.15, "oi": [3000, 3700, 4400], "iv": [24.8, 24.5, 24.3], "vol": [400, 450, 500]},
            {"strike": 1090, "delta": 0.17, "oi": [2800, 3500, 4200], "iv": [24.5, 24.2, 24.0], "vol": [380, 420, 460]},
            {"strike": 1100, "delta": 0.20, "oi": [2200, 2800, 3400], "iv": [24.0, 23.8, 23.6], "vol": [300, 340, 380]},
            # 普通 OTM
            {"strike": 1110, "delta": 0.24, "oi": [1500, 1550, 1600], "iv": [23.0, 22.9, 22.8], "vol": [200, 210, 220]},
            {"strike": 1120, "delta": 0.28, "oi": [1200, 1250, 1300], "iv": [22.0, 21.9, 21.8], "vol": [150, 160, 170]},
            {"strike": 1130, "delta": 0.32, "oi": [1000, 1050, 1100], "iv": [21.0, 20.9, 20.8], "vol": [120, 130, 140]},
            # 买Put异常（近ATM，单日暴增 + IV跳升）
            {"strike": 1150, "delta": 0.35, "oi": [1800, 2000, 6500], "iv": [25.5, 26.0, 31.0], "vol": [300, 350, 2200]},
            {"strike": 1170, "delta": 0.45, "oi": [600, 650, 700], "iv": [19.0, 18.9, 18.8], "vol": [80, 90, 100]},
        ],
    },
    "ZM": {
        "underlying": [372, 376, 380],
        "expiries": ["DEC26", "JAN27"],
        "strikes": [
            {"strike": 330, "delta": 0.13, "oi": [4000, 4800, 5600], "iv": [25.0, 24.7, 24.4], "vol": [500, 520, 540]},
            {"strike": 335, "delta": 0.16, "oi": [3600, 4300, 5000], "iv": [24.6, 24.3, 24.0], "vol": [460, 480, 500]},
            {"strike": 340, "delta": 0.19, "oi": [3000, 3600, 4200], "iv": [24.0, 23.8, 23.6], "vol": [400, 420, 440]},
            {"strike": 345, "delta": 0.22, "oi": [2400, 2900, 3400], "iv": [23.4, 23.2, 23.0], "vol": [320, 340, 360]},
            {"strike": 350, "delta": 0.27, "oi": [1500, 1550, 1600], "iv": [22.5, 22.4, 22.3], "vol": [200, 210, 220]},
            {"strike": 360, "delta": 0.33, "oi": [1200, 1250, 1300], "iv": [21.5, 21.4, 21.3], "vol": [150, 160, 170]},
            {"strike": 372, "delta": 0.40, "oi": [1500, 1600, 5200], "iv": [24.0, 25.0, 30.0], "vol": [300, 300, 2000]},
        ],
    },
    "ZC": {
        "underlying": [512, 516, 520],
        "expiries": ["DEC26", "MAR27"],
        "strikes": [
            {"strike": 450, "delta": 0.12, "oi": [25000, 30000, 35000], "iv": [23.0, 22.7, 22.4], "vol": [3000, 3200, 3400]},
            {"strike": 460, "delta": 0.15, "oi": [22000, 26000, 30000], "iv": [22.6, 22.3, 22.0], "vol": [2600, 2800, 3000]},
            {"strike": 470, "delta": 0.19, "oi": [18000, 21000, 24000], "iv": [22.0, 21.8, 21.6], "vol": [2000, 2200, 2400]},
            {"strike": 480, "delta": 0.22, "oi": [14000, 16000, 18000], "iv": [21.4, 21.2, 21.0], "vol": [1500, 1600, 1700]},
            {"strike": 490, "delta": 0.27, "oi": [10000, 10300, 10600], "iv": [20.8, 20.7, 20.6], "vol": [1000, 1100, 1200]},
            {"strike": 500, "delta": 0.33, "oi": [9000, 9500, 10000], "iv": [20.0, 19.9, 19.8], "vol": [900, 950, 1000]},
            {"strike": 505, "delta": 0.38, "oi": [9000, 10000, 30000], "iv": [22.0, 23.0, 28.0], "vol": [2000, 2200, 12000]},
        ],
    },
    "ZL": {
        "underlying": [50.5, 51.0, 51.5],
        "expiries": ["DEC26", "JAN27"],
        "strikes": [
            {"strike": 45, "delta": 0.12, "oi": [9000, 11000, 13000], "iv": [26.0, 25.7, 25.4], "vol": [1200, 1300, 1400]},
            {"strike": 46, "delta": 0.16, "oi": [8000, 9800, 11600], "iv": [25.5, 25.2, 25.0], "vol": [1000, 1100, 1200]},
            {"strike": 47, "delta": 0.20, "oi": [6500, 8000, 9500], "iv": [25.0, 24.8, 24.6], "vol": [800, 900, 1000]},
            {"strike": 48, "delta": 0.26, "oi": [4000, 4200, 4400], "iv": [24.0, 23.9, 23.8], "vol": [500, 520, 540]},
            {"strike": 49, "delta": 0.32, "oi": [3000, 3200, 3400], "iv": [23.0, 22.9, 22.8], "vol": [400, 420, 440]},
            {"strike": 50, "delta": 0.42, "oi": [4000, 4500, 14000], "iv": [25.0, 26.0, 31.0], "vol": [800, 900, 5000]},
        ],
    },
}

HEADER = ["date", "product", "expiry", "strike", "type", "oi", "oi_change",
          "delta", "iv", "volume", "underlying"]


def build_rows():
    rows = []
    for prod, cfg in PRODUCTS.items():
        und = cfg["underlying"]
        for ei, exp in enumerate(cfg["expiries"]):
            for s in cfg["strikes"]:
                for di, date in enumerate(DATES):
                    oi = s["oi"][di]
                    prev = s["oi"][di - 1] if di > 0 else oi
                    oi_chg = oi - prev
                    iv = s["iv"][di]
                    vol = s["vol"][di]
                    rows.append({
                        "date": date, "product": prod, "expiry": exp,
                        "strike": s["strike"], "type": "P",
                        "oi": oi, "oi_change": oi_chg,
                        "delta": s["delta"], "iv": iv,
                        "volume": vol, "underlying": und[di],
                    })
    return rows


def main():
    rows = build_rows()
    # 每个交易日一个文件
    for date in DATES:
        path = os.path.join(INCOMING, f"cbot_options_{date}.csv")
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=HEADER)
            w.writeheader()
            for r in rows:
                if r["date"] == date:
                    w.writerow(r)
        print("wrote", path, "rows", sum(1 for r in rows if r["date"] == date))


if __name__ == "__main__":
    main()
