"""Black-76: European options on a forward (VIX options settle on the VIX future
for their expiry, so the forward is that future, not spot VIX).

    C = e^{-rT} [F N(d1) - K N(d2)],  P = e^{-rT} [K N(-d2) - F N(-d1)]
    d1 = [ln(F/K) + sigma^2 T / 2] / (sigma sqrt T),   d2 = d1 - sigma sqrt T

T in years, sigma as a decimal (VVIX 90 -> 0.90).
"""
from __future__ import annotations

import numpy as np
from scipy.stats import norm


def black76(F, K, T, sigma, r, is_call):
    F, K, T, sigma = (np.asarray(x, dtype=float) for x in (F, K, T, sigma))
    is_call = np.asarray(is_call, dtype=bool)
    T = np.maximum(T, 1e-9)
    sigma = np.maximum(sigma, 1e-9)
    disc = np.exp(-r * T)
    sq = sigma * np.sqrt(T)
    d1 = (np.log(F / K) + 0.5 * sq ** 2) / sq
    d2 = d1 - sq
    call = disc * (F * norm.cdf(d1) - K * norm.cdf(d2))
    put = disc * (K * norm.cdf(-d2) - F * norm.cdf(-d1))
    return np.where(is_call, call, put)


def implied_vol(price, F, K, T, r, is_call, lo=1e-3, hi=5.0, tol=1e-8):
    """Bisection; returns nan if the price is outside no-arbitrage bounds."""
    intrinsic = max(F - K, 0.0) if is_call else max(K - F, 0.0)
    if price < intrinsic * np.exp(-r * T) - 1e-12:
        return float("nan")
    for _ in range(200):
        mid = 0.5 * (lo + hi)
        if black76(F, K, T, mid, r, is_call) > price:
            hi = mid
        else:
            lo = mid
        if hi - lo < tol:
            break
    return 0.5 * (lo + hi)
