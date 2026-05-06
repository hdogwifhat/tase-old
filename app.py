from flask import Flask, jsonify, send_from_directory, request
import yfinance as yf
import json, os, time, math, threading
from dotenv import load_dotenv

load_dotenv()

# Patch requests timeout before any outbound calls
import requests as _req_mod
_orig_request = _req_mod.Session.request
def _request_with_timeout(self, method, url, **kwargs):
    kwargs.setdefault('timeout', 30)
    return _orig_request(self, method, url, **kwargs)
_req_mod.Session.request = _request_with_timeout

from redis_client import rget, rset, rttl, is_available

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
app      = Flask(__name__, static_folder=BASE_DIR)
app.config['SEND_FILE_MAX_AGE_DEFAULT'] = 0

_refresh_lock = threading.Lock()

def _background_refresh():
    """Fetch fresh stock data in a background thread and write to Redis/file."""
    if not _refresh_lock.acquire(blocking=False):
        return
    try:
        import sys
        sys.path.insert(0, BASE_DIR)
        from worker import fetch_stocks, STOCK_TTL
        stocks = fetch_stocks()
        if stocks:
            payload = {'data': stocks, 'timestamp': time.time()}
            rset('tase:stocks', payload, ttl=STOCK_TTL)
            with open(CACHE_FILE, 'w', encoding='utf-8') as f:
                json.dump(payload, f)
            print('[App] Background refresh complete.', flush=True)
    except Exception as e:
        print(f'[App] Background refresh error: {e}', flush=True)
    finally:
        _refresh_lock.release()

CACHE_FILE   = os.path.join(BASE_DIR, 'tase_cache.json')
ENRICH_FILE  = os.path.join(BASE_DIR, 'enrich_cache.json')
DETAIL_TTL   = 3600
MKT_TTL      = 300
SPARK_TTL    = 3600


# ── File-based fallbacks (used when Redis is not configured) ──────────────────

def _file_load(path):
    if not os.path.exists(path):
        return None, None
    try:
        with open(path, 'r', encoding='utf-8') as f:
            c = json.load(f)
        return c.get('data'), c.get('timestamp', 0)
    except Exception:
        return None, None


def _stocks_from_redis_or_file():
    """Return (data_list, timestamp) from Redis if available, else JSON file."""
    cached = rget('tase:stocks')
    if cached:
        return cached.get('data', []), cached.get('timestamp', 0)
    data, ts = _file_load(CACHE_FILE)
    return (data or []), (ts or 0)


def _enrich_from_redis_or_file():
    """Return enrichment dict from Redis if available, else JSON file."""
    cached = rget('tase:enrich')
    if cached:
        return cached.get('data', {})
    data, _ = _file_load(ENRICH_FILE)
    return data or {}


# ── Enrichment merge ──────────────────────────────────────────────────────────

def merge_enrichment(stocks, enrich_data=None):
    if enrich_data is None:
        enrich_data = _enrich_from_redis_or_file()
    out = []
    for s in stocks:
        row = dict(s)
        e = enrich_data.get(s['ticker'], {})
        # Override company name with FMP-sourced canonical name when available.
        # yfinance sometimes returns US ticker names for dual-listed Israeli stocks
        # (e.g. CYBR.TA → "PALO ALTO NETWORKS" instead of "CyberArk Software").
        if e.get('company_name'):
            row['name'] = e['company_name']
        row['sector']         = e.get('sector')         or row.get('sector', '')
        row['industry']       = e.get('industry')
        row['ps_ratio']       = e.get('ps_ratio')
        row['pb_ratio']       = e.get('pb_ratio')
        row['ev_ebitda']      = e.get('ev_ebitda')
        row['debt_equity']    = e.get('debt_equity')
        row['current_ratio']  = e.get('current_ratio')
        row['roe']            = e.get('roe')
        row['roa']            = e.get('roa')
        row['gross_margin']   = e.get('gross_margin')
        row['op_margin']      = e.get('op_margin')
        row['net_margin']     = e.get('net_margin')
        row['revenue_growth'] = e.get('revenue_growth')
        row['eps_growth']     = e.get('eps_growth')
        row['forward_pe']     = e.get('forward_pe')
        row['beta']           = e.get('beta')
        row['ipo_date']       = e.get('ipo_date')
        row['analyst_rating'] = e.get('analyst_rating')
        row['analyst_buy']    = e.get('analyst_buy')
        row['analyst_hold']   = e.get('analyst_hold')
        row['analyst_sell']   = e.get('analyst_sell')
        row['price_target']   = e.get('price_target')
        # Phase 2 metrics
        row['quick_ratio']    = e.get('quick_ratio')
        row['roic']           = e.get('roic')
        row['ev_fcf']         = e.get('ev_fcf')
        row['wacc']           = e.get('wacc')
        # Phase 3: Real estate metrics
        row['ffo']            = e.get('ffo')
        row['affo']           = e.get('affo')
        row['p_ffo']          = e.get('p_ffo')
        row['ffo_yield']      = e.get('ffo_yield')
        row['cap_rate_implied']= e.get('cap_rate_implied')
        row['nav_discount']   = e.get('nav_discount')
        out.append(row)
    return out


# ── Stock detail helpers ──────────────────────────────────────────────────────

def _safe_num(v):
    if v is None: return None
    try:
        f = float(v)
        return None if (math.isnan(f) or math.isinf(f)) else int(f)
    except (TypeError, ValueError):
        return None


def _parse_fin_df(df):
    if df is None or getattr(df, 'empty', True):
        return {'years': [], 'rows': []}
    try:
        cols  = list(df.columns[:4])[::-1]
        years = [str(c.year) for c in cols]
        rows  = []
        for idx in df.index:
            values = []
            for c in cols:
                try:
                    v = df.at[idx, c] if c in df.columns else None
                    values.append(_safe_num(v))
                except Exception:
                    values.append(None)
            if any(v is not None for v in values):
                rows.append({'name': str(idx), 'values': values})
        return {'years': years, 'rows': rows}
    except Exception:
        return {'years': [], 'rows': []}


def _fetch_news(bare_ticker, company_name=''):
    """Multi-source news: Finnhub (bare + .TA), yfinance, RSS cache, Google News fallback."""
    news = []
    key = os.getenv('FINNHUB_API_KEY', '')
    if key:
        from datetime import datetime, timedelta
        to_d   = datetime.now().strftime('%Y-%m-%d')
        from_d = (datetime.now() - timedelta(days=90)).strftime('%Y-%m-%d')
        for sym in [bare_ticker, bare_ticker + ':IL']:
            if news:
                break
            try:
                r = _req_mod.get('https://finnhub.io/api/v1/company-news',
                                 params={'symbol': sym, 'from': from_d, 'to': to_d, 'token': key},
                                 timeout=10)
                if r.status_code == 200:
                    items = r.json()
                    if isinstance(items, list):
                        news = [{'headline': i.get('headline', ''), 'source': i.get('source', ''),
                                 'url': i.get('url', ''), 'datetime': i.get('datetime', 0),
                                 'summary': (i.get('summary') or '')[:200]}
                                for i in items[:10] if i.get('headline')]
            except Exception:
                pass

    if not news:
        try:
            yf_items = yf.Ticker(bare_ticker + '.TA').news or []
            news = [{'headline': n.get('title', ''),
                     'source':   n.get('publisher', ''),
                     'url':      n.get('link', '') or n.get('url', ''),
                     'datetime': n.get('providerPublishTime', 0),
                     'summary':  ''}
                    for n in yf_items[:10] if n.get('title')]
        except Exception:
            pass

    # Try matching against cached RSS market news
    if not news and company_name:
        try:
            cached = rget('tase:market_news')
            if cached:
                rss_items = cached.get('items', [])
                name_lower = company_name.lower()
                ticker_lower = bare_ticker.lower()
                matched = [i for i in rss_items
                           if ticker_lower in i.get('title', '').lower()
                           or name_lower[:8] in i.get('title', '').lower()]
                news = [{'headline': i.get('title', ''), 'source': i.get('source', ''),
                         'url': i.get('url', ''), 'datetime': 0, 'summary': i.get('summary', '')}
                        for i in matched[:10]]
        except Exception:
            pass

    # Maya regulatory filings — always append as a navigation entry
    # (API is WAF-blocked; deep link lets user access filings directly)
    maya_url = MAYA_URL.format(ticker=bare_ticker)
    news.append({
        'headline': f'View regulatory filings on Maya (מאיה) — {bare_ticker}',
        'source': 'Maya · TASE',
        'url': maya_url,
        'datetime': 0,
        'summary': 'Official TASE disclosure system. Click to view all regulatory filings, '
                   'immediate reports, and financial statements for this company.',
        '_maya': True,  # flag for frontend to style differently
    })
    return news


# ── TASE indices ──────────────────────────────────────────────────────────────

_MAIN_INDICES = [
    {'label': 'TA-35',  'sym': 'TA35.TA',     'tase_id': 142},
    {'label': 'TA-125', 'sym': '195.TA',       'tase_id': 137},  # 195.TA = TA125-Value ETF; TA125.TA broken on Yahoo
    {'label': 'TA-90',  'sym': 'TA90.TA',      'tase_id': 168},
    {'label': 'SME-60', 'sym': 'MIDCAP50.TA',  'tase_id': 164},
]

_SECTOR_INDICES_LABELS = [
    # Keywords match against (sector + ' ' + industry).lower() from enrichment data
    ('ביטחוניות', ['aerospace', 'defense']),                         # Industrials/Aerospace & Defense
    ('טכנולוגיה',  ['technology', 'semiconductor', 'software']),     # Technology/*
    ('בנקים',      ['financial services', 'bank', 'insurance']),     # Financial Services/*
    ('נדל"ן',      ['real estate']),                                 # Real Estate/*
    ('ביומד',      ['healthcare', 'biotech', 'pharmaceutical', 'drug', 'medical']),  # Healthcare/*
    ('אנרגיה',     ['energy', 'oil & gas', 'utilities']),            # Energy/* + Utilities/*
]

def _sector_perf_from_stocks():
    """Compute sector performance directly from the cached stock universe.
    Market-cap weighted average change_pct per sector.
    Merges enrichment (which carries sector tags) before grouping.
    """
    stocks, _ = _stocks_from_redis_or_file()
    enrich    = _enrich_from_redis_or_file()          # {ticker: {sector, industry, ...}}

    result = []
    for label, keywords in _SECTOR_INDICES_LABELS:
        group = []
        for s in stocks:
            e    = enrich.get(s.get('ticker', ''), {})
            sec  = (e.get('sector')   or s.get('sector')   or '').lower()
            ind  = (e.get('industry') or s.get('industry') or '').lower()
            tags = sec + ' ' + ind
            if any(kw in tags for kw in keywords):
                group.append(s)
        if not group:
            result.append({'label': label, 'price': None, 'change_pct': None})
            continue
        total_cap = sum(s.get('market_cap') or 0 for s in group)
        if not total_cap:
            result.append({'label': label, 'price': None, 'change_pct': None})
            continue
        weighted = sum((s.get('change_pct') or 0) * (s.get('market_cap') or 0)
                       for s in group) / total_cap
        result.append({'label': label, 'price': None,
                       'change_pct': round(weighted, 2)})
    return result


def _fetch_index_quote(sym=None, tase_id=None):
    """Return {price, change_pct} for a TASE index/ETF.
    Priority: yfinance history (5d) → yfinance fast_info → TASE API.
    """
    if sym:
        # Try multi-bar history first (gives accurate daily change)
        try:
            h = yf.Ticker(sym).history(period='5d', interval='1d')
            if len(h) >= 2:
                prev = float(h['Close'].iloc[-2])
                curr = float(h['Close'].iloc[-1])
                return {'price': round(curr, 2),
                        'change_pct': round((curr - prev) / prev * 100, 2)}
        except Exception:
            pass

        # fast_info fallback — works even when history is restricted to 1 bar
        try:
            fi = yf.Ticker(sym).fast_info
            curr = float(fi.last_price)
            prev = float(fi.previous_close)
            if curr and prev:
                return {'price': round(curr, 2),
                        'change_pct': round((curr - prev) / prev * 100, 2)}
        except Exception:
            pass

    if tase_id:
        # TASE public market-data API (used by their own website)
        for url in [
            f'https://market.tase.co.il/api/index/{tase_id}/summary',
            f'https://market.tase.co.il/api/en/index/{tase_id}/major_data',
        ]:
            try:
                r = _req_mod.get(url, timeout=8,
                                 headers={'Accept': 'application/json',
                                          'Referer': 'https://market.tase.co.il/',
                                          'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'})
                if r.status_code == 200:
                    d = r.json()
                    price  = (d.get('lastPrice') or d.get('price') or
                              d.get('indexValue') or d.get('closePrice'))
                    change = (d.get('changePercent') or d.get('percentChange') or
                              d.get('dailyChangePercent'))
                    if price:
                        return {'price': round(float(price), 2),
                                'change_pct': round(float(change or 0), 2)}
            except Exception:
                pass

    return {'price': None, 'change_pct': None}


# ── Israeli market news (RSS) ─────────────────────────────────────────────────
# Note on Maya (מאיה) API: market.tase.co.il and mayaapi.tase.co.il are WAF-blocked
# for server-side requests. Maya deep links are provided per-stock in the news section.
# Maya URL pattern: https://maya.tase.co.il/reports/company?symbol=TICKER

MAYA_URL = 'https://maya.tase.co.il/reports/company?symbol={ticker}'

_RSS_FEEDS = [
    # Verified working as of Phase 3 audit
    ('Walla Finance', 'https://rss.walla.co.il/feed/22'),
    ('Ynet Capital',  'https://www.ynet.co.il/Integration/StoryRss2.xml?catid=5326'),
    ('TheMarker',     'https://www.themarker.com/cmlink/1.744'),
]


def fetch_stock_detail(ticker):
    from tase_financials import get_financials as tase_fin

    yf_sym = ticker + '.TA'
    stock  = yf.Ticker(yf_sym)
    try:
        divisor = 100 if stock.fast_info.currency == 'ILA' else 1
    except Exception:
        divisor = 1

    ohlcv = []
    try:
        hist = stock.history(period='3y', interval='1d')
        for ts, row in hist.iterrows():
            close = row.get('Close')
            if close is None or (isinstance(close, float) and math.isnan(close)):
                continue
            try:
                vol = int(float(row.get('Volume') or 0))
            except Exception:
                vol = 0
            ohlcv.append({
                'time':   ts.strftime('%Y-%m-%d'),
                'open':   round(float(row['Open'])  / divisor, 2),
                'high':   round(float(row['High'])  / divisor, 2),
                'low':    round(float(row['Low'])   / divisor, 2),
                'close':  round(float(row['Close']) / divisor, 2),
                'volume': vol,
            })
    except Exception as e:
        print(f'  [Detail] OHLCV error {ticker}: {e}')

    income   = _parse_fin_df(stock.financials)
    balance  = _parse_fin_df(stock.balance_sheet)
    cashflow = _parse_fin_df(stock.cashflow)

    try:
        enrich_data_pre = _enrich_from_redis_or_file()
        _company_name = (enrich_data_pre.get(ticker) or {}).get('name', '')
    except Exception:
        _company_name = ''
    news = _fetch_news(ticker, _company_name)

    try:
        tfin = tase_fin(ticker)
    except Exception:
        tfin = {}

    try:
        enrich_data = _enrich_from_redis_or_file()
        e = enrich_data.get(ticker, {})
    except Exception:
        e = {}

    return {
        'ohlcv':          ohlcv,
        'income':         income,
        'balance':        balance,
        'cashflow':       cashflow,
        'news':           news,
        'description':    e.get('description') or tfin.get('description'),
        'ceo':            e.get('ceo'),
        'employees':      e.get('employees')   or tfin.get('employees'),
        'website':        e.get('website')     or tfin.get('website'),
        'logo':           e.get('logo'),
        'ipo_date':       e.get('ipo_date')    or tfin.get('ipo_date'),
        'ps_ratio':       tfin.get('ps_ratio'),
        'pb_ratio':       tfin.get('pb_ratio'),
        'ev_ebitda':      tfin.get('ev_ebitda'),
        'debt_equity':    tfin.get('debt_equity'),
        'current_ratio':  tfin.get('current_ratio'),
        'roe':            tfin.get('roe'),
        'roa':            tfin.get('roa'),
        'gross_margin':   tfin.get('gross_margin'),
        'op_margin':      tfin.get('op_margin'),
        'net_margin':     tfin.get('net_margin'),
        'revenue_growth': tfin.get('revenue_growth'),
        'eps_growth':     tfin.get('eps_growth'),
        'forward_pe':     tfin.get('forward_pe'),
        'beta':           tfin.get('beta'),
        # Phase 2
        'quick_ratio':    tfin.get('quick_ratio'),
        'roic':           tfin.get('roic'),
        'ev_fcf':         tfin.get('ev_fcf'),
        'wacc':           tfin.get('wacc'),
        # Phase 3: Real estate metrics
        'ffo':            tfin.get('ffo'),
        'affo':           tfin.get('affo'),
        'p_ffo':          tfin.get('p_ffo'),
        'ffo_yield':      tfin.get('ffo_yield'),
        'cap_rate_implied': tfin.get('cap_rate_implied'),
        'nav_discount':   tfin.get('nav_discount'),
        'sector':         e.get('sector')      or tfin.get('sector'),
        'industry':       e.get('industry')    or tfin.get('industry'),
    }


# ── Market data (fetched live, cached in Redis/memory) ───────────────────────

_mkt_mem = {'data': None, 'ts': 0}

def _fetch_market_data():
    def idx_info(sym):
        try:
            h = yf.Ticker(sym).history(period='5d', interval='1d')
            if len(h) >= 2:
                prev = float(h['Close'].iloc[-2])
                curr = float(h['Close'].iloc[-1])
                return {'price': round(curr, 2),
                        'change_pct': round((curr - prev) / prev * 100, 2)}
        except Exception:
            pass
        return {'price': None, 'change_pct': None}

    def bond_yield(sym):
        try:
            p = yf.Ticker(sym).fast_info.last_price
            return round(float(p), 3) if p else None
        except Exception:
            return None

    ta35  = idx_info('^TA35')
    ta125 = idx_info('^TA125')
    # Israeli government bond yields are not available via free APIs.
    # ^TNX=US 10Y, ^FVX=US 5Y, ^IRX=US 13-week — used as global macro reference.
    il10y = bond_yield('^TNX')
    il5y  = bond_yield('^FVX')
    il2y  = bond_yield('^IRX')

    stocks, _ = _stocks_from_redis_or_file()
    vol_total = sum(s.get('volume') or 0 for s in stocks)
    advancing = sum(1 for s in stocks if (s.get('change_pct') or 0) > 0)
    declining = sum(1 for s in stocks if (s.get('change_pct') or 0) < 0)

    return {'ta35': ta35, 'ta125': ta125, 'vol_total': vol_total,
            'advancing': advancing, 'declining': declining,
            'il10y': il10y, 'il2y': il2y, 'il5y': il5y}


# ── Routes ────────────────────────────────────────────────────────────────────

@app.route('/')
def index():
    return send_from_directory(BASE_DIR, 'index.html')


@app.after_request
def no_cache_html(response):
    if request.path == '/' or request.path.startswith('/stock/') or request.path.endswith('.html'):
        response.headers['Cache-Control'] = 'no-store, max-age=0'
        response.headers['Pragma'] = 'no-cache'
    return response


@app.route('/api/stocks')
def stocks():
    data, ts = _stocks_from_redis_or_file()
    error = rget('tase:fetch_error')

    if not data:
        msg = error or ('No data yet. Run: python worker.py --once' if is_available()
                        else 'No data yet. Cache files not found.')
        return jsonify({'data': [], 'fetching': False, 'first_run': True, 'error': msg})

    age     = time.time() - ts if ts else None
    ttl_val = rttl('tase:stocks')
    stale   = (ttl_val is not None and ttl_val < 60) or (age and age > 1800)

    if stale and not _refresh_lock.locked():
        threading.Thread(target=_background_refresh, daemon=True).start()

    merged = merge_enrichment(data)
    return jsonify({
        'data':      merged,
        'cached':    True,
        'cache_age': round(age) if age else None,
        'stale':     bool(stale),
        'fetching':  False,
        'enriching': False,
        'timestamp': ts,
        'redis':     is_available(),
    })


@app.route('/api/refresh', methods=['POST'])
def refresh():
    """
    Signals the worker to refresh. If a worker process is running it will
    pick up the flag; otherwise returns instructions to run worker.py.
    """
    rset('tase:refresh_requested', True, ttl=3600)
    return jsonify({'status': 'refresh_requested',
                    'message': 'Run `python worker.py --once` if no worker is running.'})


@app.route('/api/status')
def status():
    data, ts = _stocks_from_redis_or_file()
    age = time.time() - ts if ts else None
    return jsonify({
        'fetching':    False,
        'enriching':   False,
        'cache_age':   round(age) if age else None,
        'stock_count': len(data) if data else 0,
        'error':       rget('tase:fetch_error'),
        'redis':       is_available(),
        'progress':    {'done': 0, 'total': 0},
    })


@app.route('/api/detail/<ticker>')
def detail(ticker):
    # Check Redis detail cache first
    cached = rget(f'tase:detail:{ticker}')
    if cached and cached.get('_ts') and (time.time() - cached['_ts']) < DETAIL_TTL:
        return jsonify(cached)
    try:
        result = fetch_stock_detail(ticker)
        result['_ts'] = time.time()
        rset(f'tase:detail:{ticker}', result, ttl=DETAIL_TTL)
        return jsonify(result)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/indices')
def indices():
    cached = rget('tase:indices')
    if cached and cached.get('_ts') and (time.time() - cached['_ts']) < 300:
        return jsonify({k: v for k, v in cached.items() if k != '_ts'})
    result = {
        'main':    [{**idx, **_fetch_index_quote(idx['sym'], idx.get('tase_id'))} for idx in _MAIN_INDICES],
        'sectors': _sector_perf_from_stocks(),
    }
    rset('tase:indices', {**result, '_ts': time.time()}, ttl=300)
    return jsonify(result)


@app.route('/api/news/market')
def market_news():
    cached = rget('tase:market_news')
    if cached and cached.get('_ts') and (time.time() - cached['_ts']) < 900:
        return jsonify(cached.get('items', []))
    import xml.etree.ElementTree as ET, re
    items = []
    for source, url in _RSS_FEEDS:
        try:
            r = _req_mod.get(url, timeout=8, headers={
                'User-Agent': 'Mozilla/5.0 (compatible; TASEScreener/1.0)'})
            if r.status_code == 200:
                root    = ET.fromstring(r.content)
                channel = root.find('channel')
                feed_items = channel.findall('item') if channel else root.findall('.//item')
                for item in feed_items[:6]:
                    title   = (item.findtext('title') or '').strip()
                    link    = (item.findtext('link')  or '').strip()
                    pubdate = (item.findtext('pubDate') or '').strip()
                    desc    = re.sub(r'<[^>]+>', '', item.findtext('description') or '')[:200].strip()
                    if title:
                        items.append({'source': source, 'title': title,
                                      'url': link, 'date': pubdate, 'summary': desc})
        except Exception as e:
            print(f'[News/RSS] {source}: {e}', flush=True)
    rset('tase:market_news', {'items': items, '_ts': time.time()}, ttl=900)
    return jsonify(items)


@app.route('/api/news/stock/<ticker>')
def stock_news(ticker):
    """
    Per-stock news: Finnhub → yfinance → RSS name-match → Maya deep link.
    Cached 30 min per ticker.
    """
    cache_key = f'tase:news:{ticker}'
    cached = rget(cache_key)
    if cached and cached.get('_ts') and (time.time() - cached['_ts']) < 1800:
        return jsonify(cached.get('items', []))

    # Look up company name from stocks cache
    stocks, _ = _stocks_from_redis_or_file()
    stock_row = next((s for s in stocks if s.get('ticker') == ticker), {})
    company_name = stock_row.get('name', '')

    news = _fetch_news(ticker, company_name)

    rset(cache_key, {'items': news, '_ts': time.time()}, ttl=1800)
    return jsonify(news)


@app.route('/api/market')
def market():
    # Check Redis then memory cache
    cached = rget('tase:market')
    if cached and cached.get('_ts') and (time.time() - cached['_ts']) < MKT_TTL:
        return jsonify(cached)
    if _mkt_mem['data'] and (time.time() - _mkt_mem['ts']) < MKT_TTL:
        return jsonify(_mkt_mem['data'])
    try:
        data = _fetch_market_data()
        data['_ts'] = time.time()
        rset('tase:market', data, ttl=MKT_TTL)
        _mkt_mem['data'] = data
        _mkt_mem['ts']   = time.time()
        return jsonify(data)
    except Exception as e:
        if _mkt_mem['data']:
            return jsonify(_mkt_mem['data'])
        return jsonify({'error': str(e)}), 500


@app.route('/api/sparklines')
def sparklines():
    cached = rget('tase:sparklines')
    if cached and cached.get('_ts') and (time.time() - cached.get('_ts', 0)) < SPARK_TTL:
        payload = {k: v for k, v in cached.items() if k != '_ts'}
        return jsonify(payload)
    try:
        data, _ = _stocks_from_redis_or_file()
        if not data:
            return jsonify({})
        import pandas as pd
        top100 = [s['ticker'] + '.TA' for s in data[:100]]
        hist   = yf.download(top100, period='1mo', interval='1d',
                             auto_adjust=True, progress=False, threads=True)
        closes = hist.get('Close', hist) if isinstance(hist.columns, pd.MultiIndex) else hist
        result = {}
        for sym in top100:
            tk = sym.replace('.TA', '')
            try:
                col    = sym if sym in closes.columns else tk
                prices = closes[col].dropna().tolist()
                if len(prices) >= 2:
                    first = prices[0]
                    result[tk] = [round((p - first) / first * 100, 2) for p in prices]
            except Exception:
                pass
        result['_ts'] = time.time()
        rset('tase:sparklines', result, ttl=SPARK_TTL)
        result.pop('_ts', None)
        return jsonify(result)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/index_history')
def index_history():
    sym   = request.args.get('sym', '^TA125')
    rng   = request.args.get('range', '1mo')
    cache_key = f'tase:idxhist:{sym}:{rng}'
    cached = rget(cache_key)
    if cached and cached.get('_ts') and (time.time() - cached['_ts']) < 300:
        return jsonify({'bars': cached.get('bars', [])})

    # period → yfinance period string; interval → candle size
    period_map   = {'1d':'1d','5d':'5d','1mo':'1mo','3mo':'3mo','6mo':'6mo','1y':'1y'}
    interval_map = {'1d':'5m', '5d':'30m','1mo':'1h','3mo':'1d','6mo':'1d','1y':'1wk'}
    period   = period_map.get(rng, '1mo')
    interval = interval_map.get(rng, '1d')
    try:
        h = yf.Ticker(sym).history(period=period, interval=interval)
        bars = []
        for ts, row in h.iterrows():
            close = row.get('Close')
            if close is None or (isinstance(close, float) and math.isnan(close)):
                continue
            t = int(ts.timestamp()) if hasattr(ts, 'timestamp') else int(ts.value // 1e9)
            def _fv(k):
                v = row.get(k)
                return round(float(v), 2) if v is not None and not (isinstance(v, float) and math.isnan(v)) else round(float(close), 2)
            bars.append({
                'time':  t,
                'open':  _fv('Open'),
                'high':  _fv('High'),
                'low':   _fv('Low'),
                'close': round(float(close), 2),
                'value': round(float(close), 2),   # keep for line-series fallback
            })
        result = {'bars': bars, 'interval': interval, '_ts': time.time()}
        rset(cache_key, result, ttl=300)
        return jsonify({'bars': bars, 'interval': interval})
    except Exception as e:
        return jsonify({'bars': [], 'error': str(e)}), 500


@app.route('/stock/<ticker>')
def stock_page(ticker):
    return send_from_directory(BASE_DIR, 'stock.html')


@app.route('/dcf')
def dcf_page():
    return send_from_directory(BASE_DIR, 'dcf.html')


@app.route('/api/bonds')
def bonds_api():
    """
    Return TASE corporate bonds enriched with issuer financial data.
    Bond data comes from the worker's tase:bonds Redis key.
    Issuer data is joined from the stock cache + enrichment.
    """
    cached = rget('tase:bonds')
    if not cached or not cached.get('data'):
        return jsonify({'bonds': [], 'timestamp': 0,
                        'error': 'Bond data not yet fetched. Worker must run first.'})

    bonds = cached.get('data', [])
    ts    = cached.get('timestamp', 0)

    # Build issuer lookup maps
    stocks, _  = _stocks_from_redis_or_file()
    stock_map  = {s['ticker']: s for s in stocks}
    enrich     = _enrich_from_redis_or_file()   # {ticker: {sector, de, roe, ...}}

    enriched = []
    for b in bonds:
        row    = dict(b)
        issuer = b.get('issuer', '')
        s      = stock_map.get(issuer, {})
        e      = enrich.get(issuer, {})

        row['issuer_name']    = (e.get('company_name') or s.get('name') or issuer)
        row['issuer_price']   = s.get('price')
        row['issuer_mktcap']  = s.get('market_cap')
        row['sector']         = (e.get('sector')       or s.get('sector') or '')
        row['debt_equity']    = e.get('debt_equity')
        row['current_ratio']  = e.get('current_ratio')
        row['roe']            = e.get('roe')
        row['roa']            = e.get('roa')
        row['net_margin']     = e.get('net_margin')
        row['op_margin']      = e.get('op_margin')
        row['revenue_growth'] = e.get('revenue_growth')
        row['ev_ebitda']      = e.get('ev_ebitda')
        row['analyst_rating'] = e.get('analyst_rating')
        # Premium/discount vs par (bonds should be near 100)
        pct = b.get('price_pct') or 0
        row['vs_par'] = round(pct - 100, 2)  # positive = premium, negative = discount
        # Maya filing link (uses the full bond ticker)
        row['maya_url'] = f'https://maya.tase.co.il/reports/company?symbol={b["symbol"]}'
        enriched.append(row)

    return jsonify({'bonds': enriched, 'timestamp': ts, 'count': len(enriched)})


_fx_mem = {'data': None, 'ts': 0}

@app.route('/api/fx')
def fx():
    cached = rget('tase:fx')
    if cached and cached.get('_ts') and (time.time() - cached['_ts']) < 3600:
        return jsonify({k: v for k, v in cached.items() if k != '_ts'})
    if _fx_mem['data'] and (time.time() - _fx_mem['ts']) < 3600:
        return jsonify(_fx_mem['data'])
    try:
        ticker = yf.Ticker('USDILS=X')
        rate = float(ticker.fast_info.last_price)
        change_pct = None
        try:
            h = ticker.history(period='5d', interval='1d')
            if len(h) >= 2:
                prev = float(h['Close'].iloc[-2])
                curr = float(h['Close'].iloc[-1])
                change_pct = round((curr - prev) / prev * 100, 3)
        except Exception:
            pass
        data = {'ils_per_usd': round(rate, 4), 'usd_per_ils': round(1/rate, 6),
                'change_pct': change_pct}
        payload = {**data, '_ts': time.time()}
        rset('tase:fx', payload, ttl=3600)
        _fx_mem['data'] = data; _fx_mem['ts'] = time.time()
        return jsonify(data)
    except Exception:
        return jsonify({'ils_per_usd': 3.65, 'usd_per_ils': 0.2740, 'change_pct': None})


_earn_mem = {'data': None, 'ts': 0}

@app.route('/api/earnings')
def earnings():
    cached = rget('tase:earnings')
    if cached and cached.get('_ts') and (time.time() - cached['_ts']) < 21600:
        return jsonify([v for v in cached.get('items', [])])
    if _earn_mem['data'] and (time.time() - _earn_mem['ts']) < 21600:
        return jsonify(_earn_mem['data'])
    try:
        stocks_data, _ = _stocks_from_redis_or_file()
        if not stocks_data:
            return jsonify([])
        result = []
        for s in stocks_data[:60]:
            try:
                cal = yf.Ticker(s['ticker'] + '.TA').calendar
                if isinstance(cal, dict):
                    ed = cal.get('Earnings Date')
                    if ed:
                        date_str = str(ed[0])[:10] if hasattr(ed, '__len__') else str(ed)[:10]
                        result.append({'ticker': s['ticker'], 'name': s['name'],
                                       'date': date_str, 'mkt_cap': s.get('market_cap')})
            except Exception:
                pass
        result.sort(key=lambda x: x['date'])
        rset('tase:earnings', {'items': result, '_ts': time.time()}, ttl=21600)
        _earn_mem['data'] = result; _earn_mem['ts'] = time.time()
        return jsonify(result)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


if __name__ == '__main__':
    port = int(os.getenv('TASE_PORT') or os.getenv('PORT') or 5000)
    url  = f'http://localhost:{port}'
    print(f'\n  TASE Stock Screener  ->  {url}')
    print(f'  Redis: {"connected" if is_available() else "not configured (file fallback)"}')
    import threading
    threading.Timer(1.5, lambda: __import__('webbrowser').open(url)).start()
    app.run(host='localhost', port=port, debug=False)
