#!/usr/bin/env python3
# Black-76 模型：由真实期权结算价反推隐含波动率(IV)，再算 Put Delta。
# 用途：CME 公开期权文件不含现成 Delta/IV，用真实结算价(premium)反推，得到"真实OI + 估算Delta"。
import math
from math import log, sqrt, exp, erf


def norm_cdf(x):
    return 0.5 * (1.0 + erf(x / sqrt(2.0)))


def black76_put_price(F, K, T, r, sigma):
    """F=期货价 K=行权价 T=到期年数 r=无风险利率 sigma=IV"""
    if T <= 0 or sigma <= 0:
        return max(K - F, 0.0)
    d1 = (log(F / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * sqrt(T))
    d2 = d1 - sigma * sqrt(T)
    return exp(-r * T) * (K * norm_cdf(-d2) - F * norm_cdf(-d1))


def put_delta(F, K, T, r, sigma):
    if T <= 0 or sigma <= 0:
        return -1.0 if K > F else 0.0
    d1 = (log(F / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * sqrt(T))
    return -exp(-r * T) * norm_cdf(-d1)


def call_delta(F, K, T, r, sigma):
    """Black-76 看涨期权 Delta（期货期权，忽略贴水近似为 e^{-rT} N(d1)）。
    OTM call (K>F) 时 delta∈(0,0.5)。"""
    if T <= 0 or sigma <= 0:
        return 1.0 if K < F else 0.0
    d1 = (log(F / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * sqrt(T))
    return exp(-r * T) * norm_cdf(d1)


def implied_vol_put(price, F, K, T, r, sigma_guess=0.25):
    """牛顿法反推 IV；price=期权结算价(premium)。无解返回 None。"""
    if price is None or price <= 0 or T <= 0 or F <= 0 or K <= 0:
        return None
    sigma = sigma_guess
    for _ in range(60):
        p = black76_put_price(F, K, T, r, sigma)
        if p <= 0:
            break
        d1 = (log(F / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * sqrt(T))
        vega = exp(-r * T) * F * sqrt(T) * (1.0 / sqrt(2.0 * math.pi)) * exp(-0.5 * d1 ** 2)
        if vega == 0:
            break
        diff = p - price
        if abs(diff) < 1e-6:
            return sigma
        sigma = sigma - diff / vega
        if sigma <= 0:
            sigma = 0.01
        if sigma > 5:
            sigma = 5.0
    return sigma if sigma > 0 else None
