"""
TASE Financial Data Module
==========================
Fetches complete financial data for ALL TASE-listed stocks using yfinance.
Tries stock.info pre-computed ratios first; falls back to self-computing from
raw financial statements so TASE-only stocks (banks, RE, etc.) always get data.

SQLite cache (tase_financials.db) with 24hr TTL — yfinance is called at most
once per stock per day.
"""

import os, time, math
import yfinance as yf
from redis_client import rget, rset

TTL = 86400   # 24 hr


# ── Cache (Redis-first, in-memory fallback) ───────────────────────────────────

_mem_cache = {}  # {ticker: (data, timestamp)} — process-local fallback

def _load_cache(ticker):
    # 1. Try Redis
    cached = rget(f'tase:fin:{ticker}')
    if cached and cached.get('_ts') and (time.time() - cached['_ts']) < TTL:
        return cached, cached['_ts']
    # 2. Try in-memory
    if ticker in _mem_cache:
        data, ts = _mem_cache[ticker]
        if time.time() - ts < TTL:
            return data, ts
    return None, None


def _save_cache(ticker, data):
    payload = {**data, '_ts': time.time()}
    rset(f'tase:fin:{ticker}', payload, ttl=TTL)
    _mem_cache[ticker] = (data, time.time())


# ── Value helpers ──────────────────────────────────────────────────────────

def _f(v, dec=2):
    if v is None: return None
    try:
        f = float(v)
        return None if (math.isnan(f) or math.isinf(f)) else round(f, dec)
    except Exception:
        return None

def _pct(v, dec=2):
    r = _f(v, dec=6)
    return None if r is None else round(r * 100, dec)

def _safe_num(v):
    if v is None: return None
    try:
        f = float(v)
        return None if (math.isnan(f) or math.isinf(f)) else int(f)
    except Exception:
        return None


# ── Financial statement helpers ────────────────────────────────────────────

def _find_row(df, *names):
    """Find the first matching row in a DataFrame by partial name."""
    if df is None or getattr(df, 'empty', True): return None
    for name in names:
        key = name.lower().replace(' ', '')
        matches = [idx for idx in df.index if key in str(idx).lower().replace(' ', '')]
        if matches: return df.loc[matches[0]]
    return None


def _latest(df, *names):
    """Return the most recent non-null int value for a named row."""
    row = _find_row(df, *names)
    if row is None: return None
    for col in df.columns:
        try:
            v = _safe_num(row[col] if col in df.columns else None)
            if v is not None: return v
        except Exception:
            pass
    return None


def _row_vals(df, *names, n=5):
    """Return last n values for a named row in ascending year order."""
    row = _find_row(df, *names)
    if row is None: return [None] * n
    cols = list(df.columns[:n])[::-1]
    return [_safe_num(row[c] if c in df.columns else None) for c in cols]


def _years(df, n=5):
    if df is None or getattr(df, 'empty', True): return []
    return [str(c.year) for c in list(df.columns[:n])[::-1]]


# ── Self-computed ratios (fallback when stock.info doesn't provide them) ───

def _compute_missing(ratios, info, fin, bs):
    """
    Fill in any null ratios by computing them from raw financial statements.
    Works for banks, real-estate, and other stocks where Yahoo pre-computed
    ratios may be N/A.
    """
    mkt = info.get('marketCap') or 0

    # P/S = Market Cap / Revenue
    if ratios.get('ps_ratio') is None:
        rev = _latest(fin, 'Total Revenue', 'Operating Revenue')
        if rev and rev > 0 and mkt:
            ratios['ps_ratio'] = _f(mkt / rev)

    # EV = Market Cap + Total Debt − Cash
    td   = _latest(bs, 'Total Debt', 'Long Term Debt And Capital Lease') or 0
    cash = _latest(bs, 'Cash And Cash Equivalents',
                   'Cash Cash Equivalents And Short Term Investments') or 0
    ev = mkt + td - cash if mkt else None

    # EV/EBITDA
    if ratios.get('ev_ebitda') is None and ev and ev > 0:
        ebitda = _latest(fin, 'EBITDA', 'Normalized EBITDA')
        if ebitda and ebitda > 0:
            ratios['ev_ebitda'] = _f(ev / ebitda)

    # Debt/Equity
    if ratios.get('debt_equity') is None:
        eq = _latest(bs, 'Stockholders Equity', 'Common Stock Equity')
        if td is not None and eq and eq > 0:
            ratios['debt_equity'] = _f(td / eq)

    # Current Ratio
    if ratios.get('current_ratio') is None:
        ca = _latest(bs, 'Current Assets')
        cl = _latest(bs, 'Current Liabilities')
        if ca and cl and cl > 0:
            ratios['current_ratio'] = _f(ca / cl)

    # ROE = Net Income / Equity
    if ratios.get('roe') is None:
        ni = _latest(fin, 'Net Income Common', 'Net Income')
        eq = _latest(bs, 'Stockholders Equity', 'Common Stock Equity')
        if ni is not None and eq and eq > 0:
            ratios['roe'] = round(ni / eq * 100, 2)

    # ROA = Net Income / Total Assets
    if ratios.get('roa') is None:
        ni = _latest(fin, 'Net Income Common', 'Net Income')
        ta = _latest(bs, 'Total Assets')
        if ni is not None and ta and ta > 0:
            ratios['roa'] = round(ni / ta * 100, 2)

    # Revenue Growth YoY (from last 2 years of revenue data)
    if ratios.get('revenue_growth') is None:
        revs = _row_vals(fin, 'Total Revenue', 'Operating Revenue', n=2)
        if len(revs) >= 2 and revs[-1] and revs[-2] and revs[-2] != 0:
            ratios['revenue_growth'] = round((revs[-1] - revs[-2]) / abs(revs[-2]) * 100, 2)

    # EPS Growth YoY
    if ratios.get('eps_growth') is None:
        eps_vals = _row_vals(fin, 'Diluted EPS', 'Basic EPS', n=2)
        if len(eps_vals) >= 2 and eps_vals[-1] and eps_vals[-2] and eps_vals[-2] != 0:
            ratios['eps_growth'] = round((eps_vals[-1] - eps_vals[-2]) / abs(eps_vals[-2]) * 100, 2)

    # Gross margin
    if ratios.get('gross_margin') is None:
        gp  = _latest(fin, 'Gross Profit')
        rev = _latest(fin, 'Total Revenue', 'Operating Revenue')
        if gp and rev and rev > 0:
            ratios['gross_margin'] = round(gp / rev * 100, 2)

    # Operating margin
    if ratios.get('op_margin') is None:
        oi  = _latest(fin, 'Operating Income', 'EBIT')
        rev = _latest(fin, 'Total Revenue', 'Operating Revenue')
        if oi is not None and rev and rev > 0:
            ratios['op_margin'] = round(oi / rev * 100, 2)

    # Net margin
    if ratios.get('net_margin') is None:
        ni  = _latest(fin, 'Net Income Common', 'Net Income')
        rev = _latest(fin, 'Total Revenue', 'Operating Revenue')
        if ni is not None and rev and rev > 0:
            ratios['net_margin'] = round(ni / rev * 100, 2)

    # P/B = Price / Book Value per share
    if ratios.get('pb_ratio') is None:
        price  = info.get('regularMarketPrice') or info.get('currentPrice')
        shares = info.get('sharesOutstanding')
        eq     = _latest(bs, 'Stockholders Equity', 'Common Stock Equity')
        if price and shares and eq and eq > 0:
            bvps = eq / shares
            ratios['pb_ratio'] = _f(price / bvps)


def _derive_ipo_date(info):
    """Best-effort IPO date: FMP ipoExpectedDate → yfinance firstTradeDateMilliseconds."""
    d = info.get('ipoExpectedDate') or info.get('firstTradeDateIsoString')
    if d:
        return str(d)[:10]
    ms = info.get('firstTradeDateMilliseconds') or info.get('firstTradeDateEpochUtc')
    if ms:
        try:
            from datetime import datetime, timezone
            ts = float(ms)
            if ts > 1e10:  # milliseconds
                ts /= 1000
            return datetime.fromtimestamp(ts, tz=timezone.utc).strftime('%Y-%m-%d')
        except Exception:
            pass
    return None


# ── Core fetcher ───────────────────────────────────────────────────────────

def fetch_financials(ticker: str) -> dict:
    yf_sym = ticker + '.TA'
    stock  = yf.Ticker(yf_sym)

    # Currency divisor
    try:
        currency = stock.fast_info.currency
        divisor  = 100 if currency == 'ILA' else 1
    except Exception:
        currency = 'ILS'; divisor = 1

    # stock.info — pre-computed ratios
    try: info = stock.info or {}
    except Exception: info = {}

    pe = _f(info.get('trailingPE'))
    if pe and (pe <= 0 or pe > 10000): pe = None

    # debtToEquity from yfinance is percentage form (45.6 → D/E = 0.456)
    de_raw = info.get('debtToEquity')
    de = _f(de_raw / 100) if de_raw is not None else None

    ratios = {
        'pe':             pe,
        'forward_pe':     _f(info.get('forwardPE')),
        'ps_ratio':       _f(info.get('priceToSalesTrailing12Months')),
        'pb_ratio':       _f(info.get('priceToBook')),
        'ev_ebitda':      _f(info.get('enterpriseToEbitda')),
        'debt_equity':    de,
        'current_ratio':  _f(info.get('currentRatio')),
        'roe':            _pct(info.get('returnOnEquity')),
        'roa':            _pct(info.get('returnOnAssets')),
        'revenue_growth': _pct(info.get('revenueGrowth')),
        'eps_growth':     _pct(info.get('earningsGrowth')),
        'gross_margin':   _pct(info.get('grossMargins')),
        'op_margin':      _pct(info.get('operatingMargins')),
        'net_margin':     _pct(info.get('profitMargins')),
        'beta':           _f(info.get('beta')),
        'book_value':     _f(info.get('bookValue')),
        'trailing_eps':   _f(info.get('trailingEps')),
    }

    # Financial statements
    fin = stock.financials
    bs  = stock.balance_sheet
    cf  = stock.cashflow

    # Self-compute any still-null ratios from raw statements
    _compute_missing(ratios, info, fin, bs)

    # Build multi-year financial tables
    fin_years = _years(fin) or _years(bs) or _years(cf)

    income = {
        'years':            fin_years,
        'revenue':          _row_vals(fin, 'Total Revenue', 'Operating Revenue'),
        'gross_profit':     _row_vals(fin, 'Gross Profit'),
        'operating_income': _row_vals(fin, 'Operating Income', 'EBIT'),
        'ebitda':           _row_vals(fin, 'EBITDA', 'Normalized EBITDA'),
        'net_income':       _row_vals(fin, 'Net Income Common', 'Net Income'),
        'eps_diluted':      _row_vals(fin, 'Diluted EPS'),
    }
    balance = {
        'years':        fin_years,
        'total_assets': _row_vals(bs, 'Total Assets'),
        'total_debt':   _row_vals(bs, 'Total Debt', 'Long Term Debt'),
        'cash':         _row_vals(bs, 'Cash And Cash Equivalents',
                                  'Cash Cash Equivalents And Short Term Investments'),
        'equity':       _row_vals(bs, 'Stockholders Equity', 'Common Stock Equity'),
        'total_liab':   _row_vals(bs, 'Total Liabilities Net'),
    }
    cashflow = {
        'years':        fin_years,
        'operating_cf': _row_vals(cf, 'Operating Cash Flow'),
        'capex':        _row_vals(cf, 'Capital Expenditure'),
        'free_cf':      _row_vals(cf, 'Free Cash Flow'),
        'dividends':    _row_vals(cf, 'Common Stock Dividend', 'Cash Dividends Paid'),
    }

    mkt_cap = info.get('marketCap')
    td_val  = info.get('totalDebt') or 0
    cash_v  = info.get('totalCash') or 0
    ev      = _safe_num((mkt_cap or 0) + td_val - cash_v) if mkt_cap else None

    meta = {
        'ticker':      ticker,
        'name':        info.get('longName') or info.get('shortName') or ticker,
        'currency':    currency, 'divisor': divisor,
        'sector':      info.get('sector'),
        'industry':    info.get('industry'),
        'description': (info.get('longBusinessSummary') or '')[:1000],
        'employees':   info.get('fullTimeEmployees'),
        'website':     info.get('website'),
        'country':     info.get('country'),
        'shares':      info.get('sharesOutstanding'),
        'mkt_cap':     mkt_cap, 'ev': ev,
        'ipo_date':    _derive_ipo_date(info),
        'fetched_at':  time.time(), 'source': 'yfinance+computed',
    }

    return {**meta, **ratios, 'income': income, 'balance': balance, 'cashflow': cashflow}


# ── Public API ─────────────────────────────────────────────────────────────

def get_financials(ticker: str, force_refresh: bool = False) -> dict:
    if not force_refresh:
        cached, _ = _load_cache(ticker)
        if cached:
            return cached
    data = fetch_financials(ticker)
    _save_cache(ticker, data)
    return data
