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

import argparse, time, sys, os, math
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

def _parse_quote(s):
    symbol    = s.get('symbol')
    price_ila = s.get('regularMarketPrice')
    if not symbol or not price_ila or price_ila <= 0:
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

    print('[Worker] Starting scheduler (fetch every 30 min, enrich every 24 hr)', flush=True)
    run_full(enrich=True)  # immediate run on startup

    schedule.every(30).minutes.do(run_fetch)
    schedule.every(24).hours.do(run_enrichment)

    while True:
        schedule.run_pending()
        time.sleep(10)
