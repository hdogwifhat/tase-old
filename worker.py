"""
worker.py — Standalone data worker for TASE Screener
=====================================================
Fetches TASE stock data from yfinance / FMP / Finnhub and stores
results in Redis. Decoupled from the Flask app entirely.

Deploy as a separate Render Background Worker service, or run locally
pointing at the same REDIS_URL.

Usage:
    python worker.py              # Run immediately, then schedule every 30 min
    python worker.py --once       # Run fetch+enrich once and exit
    python worker.py --once --no-enrich   # Fetch only (faster, skips enrichment)
    python worker.py --enrich-only        # Run enrichment pass only

Environment:
    REDIS_URL           Required. e.g. redis://localhost:6379 or Upstash URL.
    FINNHUB_API_KEY     Optional. For news data and analyst consensus.
    FMP_API_KEY         Optional. For company profiles.
"""

import argparse, time, sys, os, math, re as _re
from dotenv import load_dotenv

load_dotenv()

# Patch requests timeout globally before any imports use it
import requests
_orig_req = requests.Session.request
def _req_timeout(self, method, url, **kwargs):
    kwargs.setdefault('timeout', 30)
    return _orig_req(self, method, url, **kwargs)
requests.Session.request = _req_timeout

import yfinance as yf
from yfinance import EquityQuery, screen
from redis_client import rget, rset, acquire_lock, release_lock

STOCK_TTL  = 1800    # 30 min
ENRICH_TTL = 86400   # 24 hr
DETAIL_TTL = 3600    # 1 hr


# ── Stock screener ────────────────────────────────────────────────────────────

import re as _re
_BOND_WARRANT_PAT = _re.compile(
    r'-B\d|\.B\d|'      # bond series: -B7, -B22, .B1
    r'-P\d|\.P\d|'      # warrant series: -P5
    r'-C\d|'            # convertible bond series
    r'-M\d|'            # mortgage bond series
    r'[A-Z]+-B\d\d?\.TA$'  # e.g. MLRN-B7.TA, MLSR-B22.TA
)

def _parse_quote(s):
    symbol    = s.get('symbol')
    price_ila = s.get('regularMarketPrice')
    if not symbol or not price_ila or price_ila <= 0:
        return None
    # Filter out bonds, warrants, and structured products — not equities
    if _BOND_WARRANT_PAT.search(symbol):
        return None
    divide     = s.get('currency', '') == 'ILA'
    price_ils  = round(price_ila / 100, 2) if divide else round(price_ila, 2)
    change_pct = s.get('regularMarketChangePercent')
    high_raw   = s.get('fiftyTwoWeekHigh')
    low_raw    = s.get('fiftyTwoWeekLow')
    high52     = round(high_raw / 100, 2) if (divide and high_raw) else high_raw
    low52      = round(low_raw  / 100, 2) if (divide and low_raw)  else low_raw
    market_cap = s.get('marketCap')
    eps        = s.get('epsTrailingTwelveMonths')
    pe         = s.get('trailingPE')
    volume     = s.get('regularMarketVolume')
    div_rate   = s.get('trailingAnnualDividendRate')
    div_yield  = round(div_rate / price_ils * 100, 2) if (div_rate and price_ils) else None
    name       = s.get('longName') or s.get('shortName') or symbol

    # 3M ADV in ILS: yfinance provides averageDailyVolume3Month (share count)
    # Multiply by price to get ILS turnover
    avg_vol_3m = s.get('averageDailyVolume3Month') or s.get('averageDailyVolume10Day')
    adv_ils = None
    if avg_vol_3m and price_ils:
        raw_adv = avg_vol_3m * price_ils
        # ILA stocks: already divided price by 100, volume is in shares
        adv_ils = int(raw_adv)

    return {
        'ticker':      symbol.replace('.TA', ''),
        'name':        name,
        'price':       price_ils,
        'change_pct':  round(change_pct, 2) if change_pct is not None else None,
        'market_cap':  int(market_cap) if market_cap else None,
        'pe':          round(pe, 1) if pe and 0 < pe < 10000 else None,
        'eps':         round(eps, 2) if eps is not None else None,
        'volume':      int(volume) if volume else None,
        'week52_high': high52,
        'week52_low':  low52,
        'div_yield':   div_yield,
        'adv_ils':     adv_ils,   # 3-month average daily volume in ILS (Phase 2)
        'sector':      '',
    }


def fetch_stocks():
    print('[Worker] Fetching TASE screener data...', flush=True)
    q = EquityQuery('eq', ['exchange', 'TLV'])
    all_quotes, offset, page_size = [], 0, 100
    while True:
        print(f'[Worker]   offset={offset}', flush=True)
        result = screen(q, sortField='intradaymarketcap', sortAsc=False,
                        offset=offset, size=page_size)
        quotes = result.get('quotes', [])
        if not quotes:
            break
        all_quotes.extend(quotes)
        total = result.get('total', 0)
        offset += page_size
        if offset >= total:
            break
    stocks = [p for p in (_parse_quote(s) for s in all_quotes) if p]
    stocks.sort(key=lambda x: x.get('market_cap') or 0, reverse=True)
    print(f'[Worker] Fetched {len(stocks)} stocks.', flush=True)
    return stocks


# ── Fetch task ────────────────────────────────────────────────────────────────

def run_fetch():
    if not acquire_lock('fetch', ttl=300):
        print('[Worker] Fetch lock held by another process — skipping.', flush=True)
        return None
    try:
        stocks = fetch_stocks()
        if stocks:
            rset('tase:stocks', {'data': stocks, 'timestamp': time.time()}, ttl=STOCK_TTL)
            rset('tase:fetch_error', None)
            print('[Worker] Stocks stored in Redis.', flush=True)
            return stocks
        else:
            rset('tase:fetch_error', 'No data returned from Yahoo Finance')
            return None
    except Exception as e:
        err = str(e)
        print(f'[Worker] Fetch error: {err}', flush=True)
        rset('tase:fetch_error', err)
        return None
    finally:
        release_lock('fetch')


# ── Enrichment task ───────────────────────────────────────────────────────────

def run_enrichment(stocks=None):
    if not acquire_lock('enrich', ttl=7200):
        print('[Worker] Enrich lock held — skipping.', flush=True)
        return
    try:
        if stocks is None:
            cached = rget('tase:stocks')
            stocks = cached.get('data', []) if cached else []
        if not stocks:
            print('[Worker] No stocks to enrich.', flush=True)
            return
        from enrichment import build_enrichment
        enrich = build_enrichment(stocks)
        rset('tase:enrich', {'data': enrich, 'timestamp': time.time()}, ttl=ENRICH_TTL)
        print('[Worker] Enrichment stored in Redis.', flush=True)
    except Exception as e:
        print(f'[Worker] Enrichment error: {e}', flush=True)
    finally:
        release_lock('enrich')


# ── Bond fetcher ──────────────────────────────────────────────────────────────
# Corporate bonds on TASE follow the convention TICKER-BN.TA (e.g. LUMI-B7.TA)
# B = bond series, P = warrant (כתב אופציה), C = convertible bond

_BOND_SERIES_PAT = _re.compile(r'^([A-Z]+)[-.]B(\d+)\.TA$')  # strict: only -B bonds

def _parse_bond(s):
    sym = s.get('symbol', '')
    m = _BOND_SERIES_PAT.match(sym)
    if not m: return None

    issuer_tk = m.group(1)
    series_n  = int(m.group(2))

    price_ila = s.get('regularMarketPrice')
    if not price_ila or price_ila <= 0: return None

    # For TASE bonds: par = 1 ILS per unit; yfinance reports price in ILA (agorot)
    # 1 ILS = 100 ILA, so price 102.60 ILA means the bond trades at 102.60% of par.
    # We display this as-is (NOT divided by 100) — standard Israeli bond market notation.
    price_pct  = round(float(price_ila), 2)     # = % of par (e.g. 102.60)
    chg        = s.get('regularMarketChangePercent')
    vol        = s.get('regularMarketVolume')
    mktcap     = s.get('marketCap')              # total outstanding value in ILS
    h52        = s.get('fiftyTwoWeekHigh')       # also in ILA = % of par at 52W high
    l52        = s.get('fiftyTwoWeekLow')
    name       = s.get('longName') or s.get('shortName') or ''

    return {
        'symbol':        sym.replace('.TA', ''),
        'issuer':        issuer_tk,
        'series':        f'B{series_n}',
        'series_num':    series_n,
        'name':          name,
        'price_pct':     price_pct,
        'change_pct':    round(float(chg), 2) if chg is not None else None,
        'volume':        int(vol)    if vol    else None,
        'market_value':  int(mktcap) if mktcap else None,  # total outstanding (ILS)
        'high52_pct':    round(float(h52), 2) if h52 else None,
        'low52_pct':     round(float(l52), 2) if l52 else None,
    }


def fetch_bonds():
    """Fetch all corporate bonds (-BN.TA) from TASE and store in Redis."""
    print('[Worker] Fetching TASE bond data…', flush=True)
    q = EquityQuery('eq', ['exchange', 'TLV'])
    all_quotes, offset = [], 0
    while True:
        result = screen(q, sortField='intradaymarketcap', sortAsc=False, offset=offset, size=100)
        quotes = result.get('quotes', [])
        if not quotes: break
        all_quotes.extend(quotes)
        offset += 100
        if offset >= result.get('total', 0): break

    bonds = [b for b in (_parse_bond(s) for s in all_quotes) if b]
    bonds.sort(key=lambda x: x.get('market_value') or 0, reverse=True)
    print(f'[Worker] Found {len(bonds)} corporate bonds.', flush=True)
    rset('tase:bonds', {'data': bonds, 'timestamp': time.time()}, ttl=3600)
    return bonds


# ── Index chart pre-cache ─────────────────────────────────────────────────────
# Pre-fetching index chart data eliminates the 3-8 second yfinance latency
# that users experience when the homepage chart has to fetch on-demand.

_IDX_CHART_SYMS   = ['TA35.TA', 'TA90.TA', '195.TA']
_IDX_CHART_RANGES = {
    '5d':  ('5d',  '30m', 360),    # (yf period, yf interval, cache TTL seconds)
    '1mo': ('1mo', '1h',  3600),
    '3mo': ('3mo', '1d',  3600),
    '6mo': ('6mo', '1d',  86400),
    '1y':  ('1y',  '1wk', 86400),
}

def prefetch_index_charts():
    """Pre-populate Redis with index chart OHLCV so frontend reads are instant."""
    print('[Worker] Pre-caching index charts…', flush=True)
    for sym in _IDX_CHART_SYMS:
        for rng, (period, interval, ttl) in _IDX_CHART_RANGES.items():
            cache_key = f'tase:idxhist:{sym}:{rng}'
            # Skip if still fresh
            cached = rget(cache_key)
            age = time.time() - (cached.get('_ts', 0) if cached else 0)
            if cached and cached.get('bars') and age < ttl * 0.8:
                continue
            try:
                h = yf.Ticker(sym).history(period=period, interval=interval)
                bars = []
                for ts, row in h.iterrows():
                    close = row.get('Close')
                    if close is None or (isinstance(close, float) and math.isnan(close)):
                        continue
                    t = int(ts.timestamp()) if hasattr(ts, 'timestamp') else int(ts.value // 1e9)
                    def _fv(k, _c=close):
                        v = row.get(k)
                        return round(float(v), 2) if v is not None and not (isinstance(v, float) and math.isnan(v)) else round(float(_c), 2)
                    bars.append({'time': t, 'open': _fv('Open'), 'high': _fv('High'),
                                 'low': _fv('Low'), 'close': round(float(close), 2),
                                 'value': round(float(close), 2)})
                rset(cache_key, {'bars': bars, 'interval': interval, '_ts': time.time()}, ttl=ttl)
                print(f'[Worker]   {sym} {rng}: {len(bars)} bars cached.', flush=True)
            except Exception as e:
                print(f'[Worker]   {sym} {rng}: error — {e}', flush=True)
    print('[Worker] Index chart pre-cache done.', flush=True)


# ── Full run ──────────────────────────────────────────────────────────────────

def run_full(enrich=True):
    print(f'[Worker] === Full run at {time.strftime("%Y-%m-%d %H:%M:%S")} ===', flush=True)
    stocks = run_fetch()
    if enrich:
        run_enrichment(stocks)


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='TASE screener data worker')
    parser.add_argument('--once',         action='store_true', help='Run once and exit')
    parser.add_argument('--no-enrich',    action='store_true', help='Skip enrichment pass')
    parser.add_argument('--enrich-only',  action='store_true', help='Enrichment pass only')
    args = parser.parse_args()

    from redis_client import is_available
    if not is_available():
        print('[Worker] ERROR: Redis not available. Set REDIS_URL env var.', flush=True)
        sys.exit(1)

    if args.enrich_only:
        run_enrichment()
        sys.exit(0)

    if args.once:
        run_full(enrich=not args.no_enrich)
        sys.exit(0)

    # Scheduler mode — runs indefinitely
    try:
        import schedule
    except ImportError:
        print('[Worker] Missing "schedule" package. Run: pip install schedule', flush=True)
        sys.exit(1)

    print('[Worker] Starting scheduler (5 min during market hours, 60 min otherwise, enrich every 24 hr)', flush=True)
    run_full(enrich=True)  # immediate run on startup

    import datetime as _dt

    def _smart_fetch():
        """Fetch every 5 min during TASE market hours (09:30–17:30 IL, Sun–Thu), else every 60 min."""
        now_il = _dt.datetime.utcnow() + _dt.timedelta(hours=3)  # UTC+3 approximation
        weekday = now_il.weekday()  # Mon=0…Fri=4, Sat=5, Sun=6
        in_market_week = weekday in (0, 1, 2, 3, 6)  # Sun=6, Mon-Thu=0-3
        market_open  = now_il.replace(hour=9,  minute=30, second=0, microsecond=0)
        market_close = now_il.replace(hour=17, minute=30, second=0, microsecond=0)
        in_market_hours = in_market_week and (market_open <= now_il <= market_close)
        return in_market_hours

    last_fetch_ts = time.time()
    schedule.every(24).hours.do(run_enrichment)

    while True:
        now = time.time()
        in_hours = _smart_fetch()
        interval = 300 if in_hours else 3600  # 5 min or 60 min
        if now - last_fetch_ts >= interval:
            run_fetch()
            last_fetch_ts = time.time()
        schedule.run_pending()
        time.sleep(10)
