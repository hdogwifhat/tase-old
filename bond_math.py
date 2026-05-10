"""
bond_math.py — Fixed-income math utilities for TASE corporate bonds.
====================================================================

Israeli corporate bonds (אג"ח קונצרניות) conventions:
  • Par / face value = 1 ILS per unit; prices quoted as % of par (e.g. 102.5)
  • Typically semi-annual coupon payments (freq=2)
  • May be CPI-linked (צמוד מדד); use the REAL coupon rate if the bond is indexed
  • Settlement: T+1 on TASE
  • Day-count: Actual/365 (simplified to annual years here)

None of these functions call any external API — all pure arithmetic.
"""

import math


# ── Internal helpers ──────────────────────────────────────────────────────────

def _price_from_y(y_per, coupon_per, n_periods, face):
    """
    Compute theoretical clean price given a periodic yield y_per.

    P = Σ_{t=1}^{n} C / (1+y)^t  +  F / (1+y)^n

    n_periods is rounded to the nearest integer so that partial stub periods
    (e.g. 3.5 years * 2 = 7.0) are handled cleanly without floating-point drift.
    """
    n = round(n_periods)
    if n <= 0:
        return face  # matured bond worth par
    pv_coupons = sum(coupon_per / (1 + y_per) ** t for t in range(1, n + 1))
    pv_face    = face / (1 + y_per) ** n
    return pv_coupons + pv_face


def _dprice_dy(y_per, coupon_per, n_periods, face):
    """
    Numerical first derivative dP/dy  — used for Newton-Raphson step.
    Central difference with a tiny step avoids analytical complexity.
    """
    h = 1e-7
    return (_price_from_y(y_per + h, coupon_per, n_periods, face) -
            _price_from_y(y_per - h, coupon_per, n_periods, face)) / (2 * h)


# ── Public API ────────────────────────────────────────────────────────────────

def estimate_ytm(price_pct, coupon_rate, years_to_maturity, face=100.0, freq=2):
    """
    Estimate the annualised Yield to Maturity for a TASE corporate bond.

    Solves  P = Σ C/(1+y)^t + F/(1+y)^n  for y  using Newton-Raphson
    iteration seeded with the Yield Approximation Formula as the initial guess.

    Args:
        price_pct         : Market price as % of par  (e.g. 102.5 for a bond
                            trading at 102.5 ILA per 100 ILA face value)
        coupon_rate       : Annual coupon as % of face (e.g. 4.5 for 4.5% p.a.)
        years_to_maturity : Years remaining to maturity (e.g. 3.5)
        face              : Normalised par value — default 100 (prices are %)
        freq              : Coupon payments per year: 1=annual, 2=semi-annual (default)

    Returns:
        Annualised YTM as a percentage rounded to 4 dp  (e.g. 3.8742),
        or None when inputs are invalid or the solver diverges.

    Accuracy:
        Typically < 0.001% error for normal bond parameters.
        Maximum 100 Newton-Raphson iterations; convergence threshold 1e-8.

    Expected relationships (sanity checks):
        price < par  →  YTM > coupon_rate   (discount bond)
        price = par  →  YTM = coupon_rate   (par bond)
        price > par  →  YTM < coupon_rate   (premium bond)

    Israeli bond notes:
        • Prices in our bond hub are already 'price_pct' (% of par)
        • For CPI-linked bonds, supply the REAL coupon rate
        • This formula ignores accrued interest (use as approximation)
    """
    # ── Input validation ──────────────────────────────────────────────────
    try:
        P        = float(price_pct)
        c_annual = float(coupon_rate)
        n_years  = float(years_to_maturity)
    except (TypeError, ValueError):
        return None
    if P <= 0 or c_annual < 0 or n_years <= 0 or freq not in (1, 2, 4, 12):
        return None

    coupon_per = (c_annual / 100) * face / freq   # cash per coupon period
    n_periods  = n_years * freq                    # total coupon periods

    # ── Initial guess: Yield Approximation Formula (error ~1-5%) ─────────
    # y ≈ ( C + (F-P)/n ) / ( (F+P)/2 )
    annual_guess = (
        (c_annual / 100 * face + (face - P) / n_years) / ((face + P) / 2)
    )
    y = max(annual_guess / freq, 1e-6)   # convert to periodic; keep positive

    # ── Newton-Raphson iteration ──────────────────────────────────────────
    MAX_ITER  = 100
    TOLERANCE = 1e-8   # convergence on periodic yield

    for iteration in range(MAX_ITER):
        f    = _price_from_y(y, coupon_per, n_periods, face) - P
        df   = _dprice_dy(y, coupon_per, n_periods, face)
        if df == 0:
            break
        delta = f / df
        y    -= delta
        y     = max(y, 1e-9)        # yield must stay positive
        if abs(delta) < TOLERANCE:
            break

    ytm_annual = y * freq * 100     # periodic yield → annualised %

    # Sanity gate: reject nonsensical results
    if not (0.01 <= ytm_annual <= 99.0):
        return None

    return round(ytm_annual, 4)


def current_yield(price_pct, coupon_rate, face=100.0):
    """
    Current yield  =  annual coupon cash / market price.

    Simpler than YTM: ignores time value of money and any capital gain or
    loss at maturity.  Useful as a quick income-return indicator.

    Args:
        price_pct   : Market price as % of par
        coupon_rate : Annual coupon as % of face
        face        : Normalised par (default 100)

    Returns:
        Current yield as a percentage, or None on invalid input.
    """
    try:
        P = float(price_pct)
        c = float(coupon_rate)
        if P <= 0 or c < 0:
            return None
        return round((c / 100 * face) / P * 100, 4)
    except (TypeError, ValueError):
        return None


def price_from_ytm(ytm_pct, coupon_rate, years_to_maturity, face=100.0, freq=2):
    """
    Inverse of estimate_ytm: theoretical price for a given target YTM.

    Useful for 'at what price does this bond yield X%?' analysis, or for
    marking bond positions to a benchmark yield curve.

    Args:
        ytm_pct           : Target annualised YTM as %  (e.g. 5.0)
        coupon_rate       : Annual coupon as % of face
        years_to_maturity : Years to maturity
        face              : Normalised par (default 100)
        freq              : Coupon frequency (default 2 = semi-annual)

    Returns:
        Theoretical clean price as % of par, or None on invalid input.
    """
    try:
        y_per      = float(ytm_pct) / 100 / freq
        coupon_per = float(coupon_rate) / 100 * face / freq
        n_periods  = float(years_to_maturity) * freq
    except (TypeError, ValueError):
        return None
    if y_per <= 0 or coupon_per < 0 or n_periods <= 0:
        return None
    return round(_price_from_y(y_per, coupon_per, n_periods, face), 4)


def modified_duration(ytm_pct, coupon_rate, years_to_maturity, face=100.0, freq=2):
    """
    Modified Duration — sensitivity of bond price to a 1% change in YTM.

    Modified Duration = Macaulay Duration / (1 + y_per)
    where Macaulay Duration = Σ t * PV(CF_t) / Price

    A duration of 3.2 means: a +1% rise in yield → ~3.2% drop in price.

    Returns:
        Modified duration in years, or None on invalid input.
    """
    try:
        y_per      = float(ytm_pct) / 100 / freq
        coupon_per = float(coupon_rate) / 100 * face / freq
        n_periods  = round(float(years_to_maturity) * freq)
    except (TypeError, ValueError):
        return None
    if y_per <= 0 or coupon_per < 0 or n_periods <= 0:
        return None

    price      = _price_from_y(y_per, coupon_per, n_periods, face)
    if price <= 0:
        return None

    # Weighted average time of cash flows
    mac_dur_periods = sum(
        t * (coupon_per / (1 + y_per) ** t) for t in range(1, n_periods + 1)
    )
    mac_dur_periods += n_periods * (face / (1 + y_per) ** n_periods)
    mac_dur_periods /= price

    mac_dur_years = mac_dur_periods / freq          # convert periods → years
    mod_dur       = mac_dur_years / (1 + y_per)    # modified duration
    return round(mod_dur, 4)
