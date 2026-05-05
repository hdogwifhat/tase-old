"""
Enrichment pipeline for the TASE screener.

Data sources (in order of usage):
  1. tase_financials  — yfinance-backed, SQLite-cached (24hr TTL).
                        Covers ALL TASE stocks including TASE-only listings.
                        Provides: P/S, EV/EBITDA, P/B, D/E, Current Ratio,
                                  ROE, ROA, Revenue Growth, EPS Growth,
                                  Gross/Op/Net margins, Forward P/E, Beta.
  2. FMP stable/profile — company description, CEO, logo, IPO date.
  3. Finnhub stock/recommendation — analyst Buy/Hold/Sell counts.

Note: MAYA / TASE REST API / Bizportal are all WAF-blocked for programmatic
access and cannot be used directly.
"""

import os, json, time, requests
from redis_client import rget, rset

ENRICH_TTL   = 86400
ENRICH_TOP_N = 150      # now covers more stocks since tase_financials is free

FMP_BASE = 'https://financialmodelingprep.com/stable'
FNH_BASE = 'https://finnhub.io/api/v1'


def _fmp(): return os.getenv('FMP_API_KEY', '')
def _fnh(): return os.getenv('FINNHUB_API_KEY', '')


# ── Cache I/O (Redis-first, JSON file fallback) ────────────────────────────

_ENRICH_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'enrich_cache.json')

def load_enrich_cache():
    # 1. Redis
    cached = rget('tase:enrich')
    if cached:
        return cached.get('data'), cached.get('timestamp', 0)
    # 2. JSON file fallback
    if os.path.exists(_ENRICH_FILE):
        try:
            with open(_ENRICH_FILE, 'r', encoding='utf-8') as f:
                c = json.load(f)
            return c.get('data'), c.get('timestamp', 0)
        except Exception:
            pass
    return None, None


def save_enrich_cache(data):
    rset('tase:enrich', {'data': data, 'timestamp': time.time()}, ttl=ENRICH_TTL)


# ── HTTP helper ────────────────────────────────────────────────────────────

def _get(url, params, timeout=12):
    try:
        r = requests.get(url, params=params, timeout=timeout)
        if r.status_code == 200:
            return r.json()
    except Exception:
        pass
    return None


# ── FMP profile ────────────────────────────────────────────────────────────

def _fmp_profile(fmp_sym):
    d = _get(f'{FMP_BASE}/profile', {'symbol': fmp_sym, 'apikey': _fmp()})
    if isinstance(d, list) and d:  return d[0]
    if isinstance(d, dict) and d:  return d
    return {}


# ── Finnhub recommendation ─────────────────────────────────────────────────

def _fnh_rec(bare_sym):
    d = _get(f'{FNH_BASE}/stock/recommendation', {'symbol': bare_sym, 'token': _fnh()})
    return d[0] if isinstance(d, list) and d else {}


def _rating(rec):
    if not rec: return None
    buy   = rec.get('strongBuy', 0) + rec.get('buy', 0)
    hold  = rec.get('hold', 0)
    sell  = rec.get('sell', 0) + rec.get('strongSell', 0)
    total = buy + hold + sell
    if total == 0: return None
    if buy / total >= 0.6:  return 'Buy'
    if sell / total >= 0.4: return 'Sell'
    return 'Hold'


# ── Empty enrichment row ───────────────────────────────────────────────────

def _empty():
    return {
        'sector': None, 'industry': None,
        'description': None, 'ceo': None, 'employees': None,
        'website': None, 'logo': None, 'ipo_date': None,
        'ps_ratio': None, 'pb_ratio': None, 'ev_ebitda': None,
        'debt_equity': None, 'current_ratio': None, 'quick_ratio': None,
        'roe': None, 'roa': None, 'roic': None,
        'gross_margin': None, 'op_margin': None, 'net_margin': None,
        'revenue_growth': None, 'eps_growth': None,
        'forward_pe': None, 'beta': None,
        'ev_fcf': None, 'wacc': None,
        # Phase 3: Real estate metrics
        'ffo': None, 'affo': None, 'p_ffo': None,
        'ffo_yield': None, 'cap_rate_implied': None, 'nav_discount': None,
        'analyst_rating': None,
        'analyst_buy': None, 'analyst_hold': None, 'analyst_sell': None,
    }


# ── Main build ─────────────────────────────────────────────────────────────

def build_enrichment(yf_stocks):
    """
    Build enrichment for top ENRICH_TOP_N stocks.

    Per stock (3 calls):
      1. tase_financials.get_financials()  — SQLite-cached yfinance ratios
      2. FMP stable/profile                — company profile (1 FMP call/stock)
      3. Finnhub stock/recommendation      — analyst consensus (1 Finnhub call/stock)

    Total Finnhub: up to 150 calls — spread over ~165 s at 1.1 s sleep.
    Total FMP: up to 150 calls — within 250/day free limit.
    """
    from tase_financials import get_financials

    result = {s['ticker']: _empty() for s in yf_stocks}
    top    = yf_stocks[:ENRICH_TOP_N]
    print(f'  [Enrich] Starting for top {len(top)} stocks...')

    for i, s in enumerate(top):
        tk      = s['ticker']
        fmp_sym = tk + '.TA'
        bare    = tk

        print(f'  [Enrich] {i+1}/{len(top)}  {tk}')

        # ── 1. Financial ratios via tase_financials (yfinance + SQLite) ────
        try:
            fin = get_financials(tk)
        except Exception:
            fin = {}

        # ── 2. FMP profile ──────────────────────────────────────────────────
        prof = _fmp_profile(fmp_sym)

        # ── 3. Finnhub analyst recommendation ──────────────────────────────
        rec = _fnh_rec(bare)
        time.sleep(1.1)   # Finnhub: 60 calls/min limit

        result[tk] = {
            # Profile
            'sector':      (prof.get('sector')   or fin.get('sector')   or None),
            'industry':    (prof.get('industry') or fin.get('industry') or None),
            'description': (prof.get('description') or fin.get('description') or '')[:1000] or None,
            'ceo':         prof.get('ceo')       or None,
            'employees':   prof.get('fullTimeEmployees') or fin.get('employees') or None,
            'website':     prof.get('website')   or fin.get('website')  or None,
            'logo':        prof.get('image')     or None,
            'ipo_date':    prof.get('ipoDate')   or fin.get('ipo_date') or None,
            # Financial metrics (from tase_financials / yfinance)
            'ps_ratio':       fin.get('ps_ratio'),
            'pb_ratio':       fin.get('pb_ratio'),
            'ev_ebitda':      fin.get('ev_ebitda'),
            'debt_equity':    fin.get('debt_equity'),
            'current_ratio':  fin.get('current_ratio'),
            'quick_ratio':    fin.get('quick_ratio'),    # Phase 2
            'roe':            fin.get('roe'),
            'roa':            fin.get('roa'),
            'roic':           fin.get('roic'),           # Phase 2
            'gross_margin':   fin.get('gross_margin'),
            'op_margin':      fin.get('op_margin'),
            'net_margin':     fin.get('net_margin'),
            'revenue_growth': fin.get('revenue_growth'),
            'eps_growth':     fin.get('eps_growth'),
            'forward_pe':     fin.get('forward_pe'),
            'beta':           fin.get('beta'),
            'ev_fcf':         fin.get('ev_fcf'),         # Phase 2
            'wacc':           fin.get('wacc'),           # Phase 2
            # Phase 3: Real estate metrics
            'ffo':            fin.get('ffo'),
            'affo':           fin.get('affo'),
            'p_ffo':          fin.get('p_ffo'),
            'ffo_yield':      fin.get('ffo_yield'),
            'cap_rate_implied': fin.get('cap_rate_implied'),
            'nav_discount':   fin.get('nav_discount'),
            # Analyst (Finnhub)
            'analyst_rating': _rating(rec),
            'analyst_buy':    (rec.get('strongBuy', 0) + rec.get('buy', 0)) if rec else None,
            'analyst_hold':   rec.get('hold') if rec else None,
            'analyst_sell':   (rec.get('sell', 0) + rec.get('strongSell', 0)) if rec else None,
        }

    print(f'  [Enrich] Done — {len(result)} stocks.')
    return result
