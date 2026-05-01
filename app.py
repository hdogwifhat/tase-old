from flask import Flask, jsonify, send_from_directory, request
import yfinance as yf
from yfinance import EquityQuery, screen
import json, os, time, threading, math
import requests
from dotenv import load_dotenv

# Force a 30-second timeout on every outbound HTTP request (yfinance has no timeout by default)
_orig_request = requests.Session.request
def _request_with_timeout(self, method, url, **kwargs):
    kwargs.setdefault('timeout', 30)
    return _orig_request(self, method, url, **kwargs)
requests.Session.request = _request_with_timeout

load_dotenv()

BASE_DIR         = os.path.dirname(os.path.abspath(__file__))
app              = Flask(__name__, static_folder=BASE_DIR)
app.config['SEND_FILE_MAX_AGE_DEFAULT'] = 0

CACHE_FILE       = os.path.join(BASE_DIR, 'tase_cache.json')
DETAIL_CACHE_DIR = os.path.join(BASE_DIR, 'detail_cache')
CACHE_TTL        = 1800    # 30 min  — price data
ENRICH_TTL       = 86400   # 24 hr   — fundamentals
DETAIL_TTL       = 3600    # 1 hr    — per-stock detail
MKT_TTL          = 300     # 5 min   — index / market summary

_is_fetching  = False
_is_enriching = False
_mkt_cache    = {'data': None, 'ts': 0}


# ── yfinance screener ──────────────────────────────────────────────────────

def fetch_all_tase():
    print('[Fetch] Starting fetch_all_tase', flush=True)
    q = EquityQuery('eq', ['exchange', 'TLV'])
    all_quotes, offset, page_size = [], 0, 100
    while True:
        print(f'[Fetch] Requesting offset={offset}', flush=True)
        result = screen(q, sortField='intradaymarketcap', sortAsc=False,
                        offset=offset, size=page_size)
        quotes = result.get('quotes', [])
        print(f'[Fetch] Got {len(quotes)} quotes (total={result.get("total", "?")})', flush=True)
        if not quotes:
            break
        all_quotes.extend(quotes)
        total = result.get('total', 0)
        offset += page_size
        if offset >= total:
            break
    stocks = [p for p in (parse_quote(s) for s in all_quotes) if p]
    stocks.sort(key=lambda x: x.get('market_cap') or 0, reverse=True)
    print(f'[Fetch] Done — {len(stocks)} stocks', flush=True)
    return stocks


def parse_quote(s):
    symbol    = s.get('symbol')
    price_ila = s.get('regularMarketPrice')
    if not symbol or not price_ila or price_ila <= 0:
        return None
    divide    = s.get('currency', '') == 'ILA'
    price_ils = round(price_ila / 100, 2) if divide else round(price_ila, 2)
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


# ── Cache I/O ──────────────────────────────────────────────────────────────

def load_cache():
    if not os.path.exists(CACHE_FILE):
        return None, None
    with open(CACHE_FILE, 'r', encoding='utf-8') as f:
        c = json.load(f)
    return c.get('data'), c.get('timestamp', 0)


def save_cache(data):
    with open(CACHE_FILE, 'w', encoding='utf-8') as f:
        json.dump({'data': data, 'timestamp': time.time()}, f)


# ── Detail cache ───────────────────────────────────────────────────────────

def load_detail_cache(ticker):
    path = os.path.join(DETAIL_CACHE_DIR, f'{ticker}.json')
    if not os.path.exists(path):
        return None, None
    try:
        with open(path, 'r', encoding='utf-8') as f:
            c = json.load(f)
        return c.get('data'), c.get('timestamp', 0)
    except Exception:
        return None, None


def save_detail_cache(ticker, data):
    os.makedirs(DETAIL_CACHE_DIR, exist_ok=True)
    path = os.path.join(DETAIL_CACHE_DIR, f'{ticker}.json')
    with open(path, 'w', encoding='utf-8') as f:
        json.dump({'data': data, 'timestamp': time.time()}, f)


# ── Enrichment ─────────────────────────────────────────────────────────────

def _should_enrich():
    try:
        from enrichment import load_enrich_cache
        _, ts = load_enrich_cache()
        return ts is None or (time.time() - ts) > ENRICH_TTL
    except Exception:
        return False


def start_enrich_bg(yf_stocks):
    global _is_enriching
    if _is_enriching:
        return
    _is_enriching = True
    def bg():
        global _is_enriching
        try:
            from enrichment import build_enrichment, save_enrich_cache
            enrich = build_enrichment(yf_stocks)
            save_enrich_cache(enrich)
        except Exception as e:
            print(f'  [Enrich] Error: {e}')
        finally:
            _is_enriching = False
    threading.Thread(target=bg, daemon=True).start()


def merge_enrichment(stocks):
    try:
        from enrichment import load_enrich_cache
        enrich_data, _ = load_enrich_cache()
    except Exception:
        enrich_data = None
    out = []
    for s in stocks:
        row = dict(s)
        e = (enrich_data or {}).get(s['ticker'], {})
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
        out.append(row)
    return out


# ── Background fetch ───────────────────────────────────────────────────────

_fetch_error = None

def start_bg_fetch():
    global _is_fetching, _fetch_error
    if _is_fetching:
        return
    _is_fetching = True
    _fetch_error = None
    def bg():
        global _is_fetching, _fetch_error
        try:
            fresh = fetch_all_tase()
            if fresh:
                save_cache(fresh)
                if _should_enrich():
                    start_enrich_bg(fresh)
            else:
                _fetch_error = 'No data returned from Yahoo Finance'
        except Exception as e:
            _fetch_error = str(e)
            print(f'  [Fetch] Error: {e}')
        finally:
            _is_fetching = False
    threading.Thread(target=bg, daemon=True).start()


# ── Stock detail helpers ───────────────────────────────────────────────────

def _safe_num(v):
    if v is None:
        return None
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


def _fetch_news(bare_ticker):
    import requests
    from datetime import datetime, timedelta
    key  = os.getenv('FINNHUB_API_KEY', '')
    if not key:
        return []
    to_d   = datetime.now().strftime('%Y-%m-%d')
    from_d = (datetime.now() - timedelta(days=30)).strftime('%Y-%m-%d')
    try:
        r = requests.get('https://finnhub.io/api/v1/company-news',
                         params={'symbol': bare_ticker, 'from': from_d, 'to': to_d, 'token': key},
                         timeout=10)
        if r.status_code == 200:
            items = r.json()
            if isinstance(items, list):
                return [{'headline': i.get('headline',''), 'source': i.get('source',''),
                         'url': i.get('url',''), 'datetime': i.get('datetime',0),
                         'summary': (i.get('summary') or '')[:200]}
                        for i in items[:10] if i.get('headline')]
    except Exception:
        pass
    return []


def fetch_stock_detail(ticker):
    """
    Fetch OHLCV, financials, news, profile, and ratios for one ticker.
    Ratios come from tase_financials (self-computed from statements when
    stock.info fields are N/A — covers TASE-only banks, RE, etc.).
    """
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
    news     = _fetch_news(ticker)

    # Fresh ratios from tase_financials — self-computes missing ones from statements
    try:
        tfin = tase_fin(ticker)
    except Exception:
        tfin = {}

    # Profile fields from enrich_cache (description, CEO, logo, etc.)
    try:
        from enrichment import load_enrich_cache
        enrich_data, _ = load_enrich_cache()
        e = (enrich_data or {}).get(ticker, {})
    except Exception:
        e = {}

    return {
        'ohlcv':          ohlcv,
        'income':         income,
        'balance':        balance,
        'cashflow':       cashflow,
        'news':           news,
        # Profile (from enrich_cache / tase_financials)
        'description':    e.get('description') or tfin.get('description'),
        'ceo':            e.get('ceo'),
        'employees':      e.get('employees')   or tfin.get('employees'),
        'website':        e.get('website')     or tfin.get('website'),
        'logo':           e.get('logo'),
        'ipo_date':       e.get('ipo_date')    or tfin.get('ipo_date'),
        # Ratios — fresh from tase_financials (covers TASE-only stocks)
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
        'sector':         e.get('sector')      or tfin.get('sector'),
        'industry':       e.get('industry')    or tfin.get('industry'),
    }


# ── Market summary ─────────────────────────────────────────────────────────

def _fetch_market_data():
    """Fetch TA-35, TA-125, bond yields, FX + breadth from cache."""
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
            t = yf.Ticker(sym)
            p = t.fast_info.last_price
            return round(float(p), 3) if p else None
        except Exception:
            return None

    ta35  = idx_info('^TA35')
    ta125 = idx_info('^TA125')
    # Israeli government bond yields — try TASE bond tickers
    il10y = bond_yield('IL10Y=X') or bond_yield('^TNX')   # 10-year fallback to US
    il2y  = bond_yield('IL02Y=X')

    stocks    = load_cache()[0] or []
    vol_total = sum(s.get('volume') or 0 for s in stocks)
    advancing = sum(1 for s in stocks if (s.get('change_pct') or 0) > 0)
    declining = sum(1 for s in stocks if (s.get('change_pct') or 0) < 0)

    return {
        'ta35':      ta35,
        'ta125':     ta125,
        'vol_total': vol_total,
        'advancing': advancing,
        'declining': declining,
        'il10y':     il10y,
        'il2y':      il2y,
    }


# ── Routes ─────────────────────────────────────────────────────────────────

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
    data, ts = load_cache()
    age = time.time() - ts if ts else None
    if data is None:
        if _fetch_error and not _is_fetching:
            return jsonify({'data': [], 'fetching': False, 'first_run': True, 'error': _fetch_error})
        start_bg_fetch()
        return jsonify({'data': [], 'fetching': True, 'first_run': True})
    if age and age > CACHE_TTL:
        start_bg_fetch()
    merged = merge_enrichment(data)
    return jsonify({
        'data':      merged,
        'cached':    True,
        'cache_age': round(age) if age else None,
        'stale':     bool(age and age > CACHE_TTL),
        'fetching':  _is_fetching,
        'enriching': _is_enriching,
        'timestamp': ts,
    })


@app.route('/api/refresh', methods=['POST'])
def refresh():
    global _is_fetching
    _is_fetching = False
    start_bg_fetch()
    return jsonify({'status': 'started'})


@app.route('/api/status')
def status():
    data, ts = load_cache()
    age = time.time() - ts if ts else None
    return jsonify({
        'fetching':    _is_fetching,
        'enriching':   _is_enriching,
        'cache_age':   round(age) if age else None,
        'stock_count': len(data) if data else 0,
        'error':       _fetch_error,
        'progress':    {'done': 0, 'total': 0},
    })


@app.route('/api/detail/<ticker>')
def detail(ticker):
    cached, ts = load_detail_cache(ticker)
    if cached and ts and (time.time() - ts) < DETAIL_TTL:
        return jsonify(cached)
    try:
        result = fetch_stock_detail(ticker)
        save_detail_cache(ticker, result)
        return jsonify(result)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/market')
def market():
    now = time.time()
    if _mkt_cache['data'] and (now - _mkt_cache['ts']) < MKT_TTL:
        return jsonify(_mkt_cache['data'])
    try:
        data = _fetch_market_data()
        _mkt_cache['data'] = data
        _mkt_cache['ts']   = now
        return jsonify(data)
    except Exception as e:
        if _mkt_cache['data']:
            return jsonify(_mkt_cache['data'])
        return jsonify({'error': str(e)}), 500


# Sparkline cache: {ticker: [prices], ...}  refreshed every hour
_spark_cache = {'data': {}, 'ts': 0}
SPARK_TTL    = 3600

@app.route('/api/sparklines')
def sparklines():
    """Return 1-month daily closing prices (% change from start) for top 100 stocks."""
    now = time.time()
    if _spark_cache['data'] and (now - _spark_cache['ts']) < SPARK_TTL:
        return jsonify(_spark_cache['data'])
    try:
        data, _ = load_cache()
        if not data:
            return jsonify({})
        top100 = [s['ticker'] + '.TA' for s in data[:100]]
        import pandas as pd
        hist = yf.download(top100, period='1mo', interval='1d',
                           auto_adjust=True, progress=False, threads=True)
        closes = hist.get('Close', hist) if isinstance(hist.columns, pd.MultiIndex) else hist
        result = {}
        for sym in top100:
            tk = sym.replace('.TA', '')
            try:
                col = sym if sym in closes.columns else tk
                prices = closes[col].dropna().tolist()
                if len(prices) >= 2:
                    first = prices[0]
                    result[tk] = [round((p - first) / first * 100, 2) for p in prices]
            except Exception:
                pass
        _spark_cache['data'] = result
        _spark_cache['ts']   = now
        return jsonify(result)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/stock/<ticker>')
def stock_page(ticker):
    return send_from_directory(BASE_DIR, 'stock.html')


# FX rate cache
_fx_cache = {'data': None, 'ts': 0}

@app.route('/api/fx')
def fx():
    """Return USD/ILS exchange rate (cached 1 hour)."""
    now = time.time()
    if _fx_cache['data'] and (now - _fx_cache['ts']) < 3600:
        return jsonify(_fx_cache['data'])
    try:
        rate = float(yf.Ticker('USDILS=X').fast_info.last_price)
        data = {'ils_per_usd': round(rate, 4), 'usd_per_ils': round(1/rate, 6)}
        _fx_cache['data'] = data; _fx_cache['ts'] = now
        return jsonify(data)
    except Exception:
        return jsonify({'ils_per_usd': 3.65, 'usd_per_ils': 0.2740})


# Earnings cache
_earn_cache = {'data': None, 'ts': 0}

@app.route('/api/earnings')
def earnings():
    """Return upcoming earnings dates for top 60 stocks (cached 6 hours)."""
    now = time.time()
    if _earn_cache['data'] and (now - _earn_cache['ts']) < 21600:
        return jsonify(_earn_cache['data'])
    try:
        stocks_data, _ = load_cache()
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
        _earn_cache['data'] = result; _earn_cache['ts'] = now
        return jsonify(result)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


if __name__ == '__main__':
    port = int(os.getenv('TASE_PORT') or os.getenv('PORT') or 5000)
    url = f'http://localhost:{port}'
    print(f'\n  TASE Stock Screener  ->  {url}\n')
    threading.Timer(1.5, lambda: __import__('webbrowser').open(url)).start()
    app.run(host='localhost', port=port, debug=False)
