"""
worker.py — Standalone data worker for TASE Screener
=====================================================
Fetches TASE stock data from yfinance and stores results in Redis.
ONLY responsibility: stocks + enrichment. Nothing else.

Deploy as a separate Render Background Worker service.

Usage:
    python worker.py              # scheduler mode (5 min market hours, 60 min off)
    python worker.py --once       # run once and exit
    python worker.py --once --no-enrich
    python worker.py --enrich-only
"""

import argparse, time, sys, os, math, re as _re
from dotenv import load_dotenv

load_dotenv()

import requests
_orig_req = requests.Session.request
def _req_timeout(self, method, url, **kwargs):
    kwargs.setdefault('timeout', 30)
    return _orig_req(self, method, url, **kwargs)
requests.Session.request = _req_timeout

import yfinance as yf
from yfinance import EquityQuery, screen
from redis_client import rget, rset, acquire_lock, release_lock

STOCK_TTL  = 7200    # 2 hr — must be > off-hours refresh interval (60 min)
ENRICH_TTL = 86400   # 24 hr

# Bonds and warrants — filtered OUT of the equity screener
_BOND_WARRANT_PAT = _re.compile(r'-B\d|\.B\d|-P\d|\.P\d|-C\d|-M\d')


# ── Stock screener ────────────────────────────────────────────────────────────

def _parse_quote(s):
    symbol    = s.get('symbol')
    price_ila = s.get('regularMarketPrice')
    if not symbol or not price_ila or price_ila <= 0:
        return None
    if _BOND_WARRANT_PAT.search(symbol):   # skip bonds / warrants
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
    avg_vol_3m = s.get('averageDailyVolume3Month') or s.get('averageDailyVolume10Day')
    adv_ils    = int(avg_vol_3m * price_ils) if (avg_vol_3m and price_ils) else None

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
        'adv_ils':     adv_ils,
        'sector':      s.get('sector')   or '',
        'industry':    s.get('industry') or '',
    }


def fetch_stocks():
    print('[Worker] Fetching TASE equities…', flush=True)
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
        offset += page_size
        if offset >= result.get('total', 0):
            break
    stocks = [p for p in (_parse_quote(s) for s in all_quotes) if p]
    stocks.sort(key=lambda x: x.get('market_cap') or 0, reverse=True)
    print(f'[Worker] Fetched {len(stocks)} stocks.', flush=True)
    return stocks


# ── Fetch task ────────────────────────────────────────────────────────────────

def run_fetch():
    if not acquire_lock('fetch', ttl=300):
        print('[Worker] Fetch lock held — skipping.', flush=True)
        return None
    try:
        stocks = fetch_stocks()
        if stocks:
            payload = {'data': stocks, 'timestamp': time.time()}
            rset('tase:stocks', payload, ttl=STOCK_TTL)
            rset('tase:fetch_error', None)
            print('[Worker] Stocks stored in Redis.', flush=True)
            # File fallback: write local cache when Redis is unavailable
            from redis_client import is_available as _redis_ok
            if not _redis_ok():
                _cache = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'tase_cache.json')
                try:
                    import json as _json
                    with open(_cache, 'w', encoding='utf-8') as _f:
                        _json.dump(payload, _f)
                    print('[Worker] Wrote tase_cache.json (Redis unavailable).', flush=True)
                except Exception as _fe:
                    print(f'[Worker] File cache write error: {_fe}', flush=True)
            return stocks
        else:
            rset('tase:fetch_error', 'No data returned from Yahoo Finance')
            return None
    except Exception as e:
        print(f'[Worker] Fetch error: {e}', flush=True)
        rset('tase:fetch_error', str(e))
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


# ── Full run ──────────────────────────────────────────────────────────────────

def run_full(enrich=True):
    print(f'[Worker] === Full run {time.strftime("%Y-%m-%d %H:%M:%S")} ===', flush=True)
    stocks = run_fetch()
    if enrich:
        run_enrichment(stocks)


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--once',        action='store_true')
    parser.add_argument('--no-enrich',   action='store_true')
    parser.add_argument('--enrich-only', action='store_true')
    args = parser.parse_args()

    from redis_client import is_available
    if not is_available():
        print('[Worker] ERROR: Redis not available.', flush=True)
        sys.exit(1)

    if args.enrich_only:
        run_enrichment(); sys.exit(0)
    if args.once:
        run_full(enrich=not args.no_enrich); sys.exit(0)

    try:
        import schedule
    except ImportError:
        print('[Worker] pip install schedule', flush=True); sys.exit(1)

    print('[Worker] Scheduler starting…', flush=True)
    run_full(enrich=True)   # immediate on startup

    import datetime as _dt

    def _in_market_hours():
        now = _dt.datetime.utcnow() + _dt.timedelta(hours=3)
        dow = now.weekday()  # Mon=0…Sun=6
        if dow not in (0, 1, 2, 3, 6): return False  # Fri/Sat off
        op = now.replace(hour=9, minute=30, second=0, microsecond=0)
        cl = now.replace(hour=17, minute=30, second=0, microsecond=0)
        return op <= now <= cl

    last_fetch = time.time()
    schedule.every(24).hours.do(run_enrichment)

    while True:
        elapsed  = time.time() - last_fetch
        interval = 300 if _in_market_hours() else 3600
        if elapsed >= interval:
            run_fetch()
            last_fetch = time.time()
        schedule.run_pending()
        time.sleep(10)
