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
_bond_lock    = threading.Lock()
_APP_START    = time.time()
import re as _re_app

# ── In-process scheduler (replaces the paid Render Background Worker) ─────────
# Runs stock fetch on market-hours schedule + daily enrichment, all inside the
# web service process.  Uses Redis distributed lock so only one gunicorn worker
# actually runs the work even if multiple workers are configured.

def _scheduler():
    import datetime as _dt

    def _in_market_hours():
        now = _dt.datetime.utcnow() + _dt.timedelta(hours=3)
        if now.weekday() not in (0, 1, 2, 3, 6):   # Fri/Sat = 4/5
            return False
        op = now.replace(hour=9,  minute=30, second=0, microsecond=0)
        cl = now.replace(hour=17, minute=30, second=0, microsecond=0)
        return op <= now <= cl

    _last_fetch  = 0.0
    _last_enrich = 0.0   # 0 = run enrichment soon after first successful fetch

    time.sleep(20)       # let gunicorn finish binding before we start work
    print('[Scheduler] Started.', flush=True)

    # If Redis already has fresh stocks from a previous deploy, don't re-fetch
    # immediately — that causes rapid duplicate fetches and Yahoo Finance rate limits.
    try:
        _existing = rget('tase:stocks')
        if _existing and _existing.get('timestamp'):
            _last_fetch = float(_existing['timestamp'])
            print(f'[Scheduler] Found existing stock data — next refresh in schedule.',
                  flush=True)
    except Exception:
        pass

    while True:
        try:
            now      = time.time()
            interval = 300 if _in_market_hours() else 3600

            # ── Stock refresh ────────────────────────────────────────────
            if now - _last_fetch >= interval and not _refresh_lock.locked():
                _last_fetch = now
                threading.Thread(target=_background_refresh, daemon=True).start()

            # ── Daily enrichment (sector + fundamentals via FMP) ─────────
            enrich_cached = rget('tase:enrich')
            stocks_ready  = bool(rget('tase:stocks'))
            enrich_age    = (now - float((enrich_cached or {}).get('timestamp', 0))
                             if enrich_cached else 999999)
            if stocks_ready and enrich_age >= 86400 and not rget('lock:enrich'):
                _last_enrich = now
                def _run_enrich():
                    from redis_client import acquire_lock as _acq, release_lock as _rel
                    if not _acq('enrich', ttl=7200):
                        return
                    try:
                        cached = rget('tase:stocks')
                        stocks = (cached or {}).get('data', [])
                        if not stocks:
                            return
                        # ── Phase 1: fast yfinance sector tag (free, no API limit) ──
                        _yf_enrich = {}
                        try:
                            import yfinance as _yf2
                            for _st in stocks[:200]:
                                try:
                                    _info = _yf2.Ticker(_st['ticker'] + '.TA').info
                                    _yf_enrich[_st['ticker']] = {
                                        'sector':   _info.get('sector', '') or '',
                                        'industry': _info.get('industry', '') or '',
                                    }
                                except Exception:
                                    pass
                            if _yf_enrich:
                                # Merge into enrich cache immediately so heatmap works
                                _ex = (rget('tase:enrich') or {}).get('data', {}) or {}
                                for _tk, _v in _yf_enrich.items():
                                    _ex.setdefault(_tk, {}).update(_v)
                                rset('tase:enrich', {'data': _ex, 'timestamp': now}, ttl=86400)
                                print(f'[Scheduler] yfinance sectors: {len(_yf_enrich)} tickers.',
                                      flush=True)
                        except Exception as _ye:
                            print(f'[Scheduler] yfinance sector error: {_ye}', flush=True)

                        # ── Phase 2: FMP deep fundamentals ───────────────────────
                        try:
                            from enrichment import build_enrichment
                            enrich = build_enrichment(stocks[:100])
                            # Merge yfinance sectors into FMP result (FMP may lack some)
                            for _tk, _v in _yf_enrich.items():
                                enrich.setdefault(_tk, {})
                                if not enrich[_tk].get('sector'):
                                    enrich[_tk]['sector']   = _v.get('sector', '')
                                if not enrich[_tk].get('industry'):
                                    enrich[_tk]['industry'] = _v.get('industry', '')
                            rset('tase:enrich', {'data': enrich, 'timestamp': time.time()},
                                 ttl=86400)
                            print(f'[Scheduler] Full enrichment done — {len(enrich)} tickers.',
                                  flush=True)
                        except Exception as _fe:
                            print(f'[Scheduler] FMP enrichment error: {_fe}', flush=True)
                    except Exception as _ee:
                        print(f'[Scheduler] Enrichment error: {_ee}', flush=True)
                    finally:
                        _rel('enrich')
                threading.Thread(target=_run_enrich, daemon=True).start()

        except Exception as _se:
            print(f'[Scheduler] Error: {_se}', flush=True)

        time.sleep(30)

threading.Thread(target=_scheduler, daemon=True, name='tase-scheduler').start()

# ── Direct Yahoo Finance v8 chart API (10-20× faster than yfinance wrapper) ──

_YF_CHART_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
    'Accept': 'application/json',
}

def _fetch_chart_v8(sym, period, interval):
    """
    Call the Yahoo Finance v8 chart endpoint directly.
    Returns list of bar dicts {time,open,high,low,close,value} or None on failure.
    Much faster than the yfinance Python wrapper (~300ms vs 3-8s).
    """
    url = f'https://query1.finance.yahoo.com/v8/finance/chart/{sym}'
    params = {'interval': interval, 'range': period,
              'events': 'div,split', 'includePrePost': 'false'}
    try:
        r = _req_mod.get(url, params=params, headers=_YF_CHART_HEADERS, timeout=12)
        if r.status_code != 200:
            return None
        data   = r.json()
        result = data.get('chart', {}).get('result') or []
        if not result:
            return None
        chart  = result[0]
        stamps = chart.get('timestamp') or []
        quote  = (chart.get('indicators', {}).get('quote') or [{}])[0]
        opens  = quote.get('open',   [])
        highs  = quote.get('high',   [])
        lows   = quote.get('low',    [])
        closes = quote.get('close',  [])

        bars = []
        for i, ts in enumerate(stamps):
            c = closes[i] if i < len(closes) else None
            if c is None or (isinstance(c, float) and math.isnan(c)):
                continue
            def _g(arr, idx, fallback):
                v = arr[idx] if idx < len(arr) else None
                return fallback if (v is None or (isinstance(v, float) and math.isnan(v))) else round(float(v), 2)
            bars.append({
                'time':  int(ts),
                'open':  _g(opens, i, round(c, 2)),
                'high':  _g(highs, i, round(c, 2)),
                'low':   _g(lows,  i, round(c, 2)),
                'close': round(float(c), 2),
                'value': round(float(c), 2),
            })
        return bars or None
    except Exception:
        return None


# ── Background bond fetch (self-contained, no worker import) ─────────────────

_BOND_PAT = _re_app.compile(r'^([A-Z]+)-B(\d+)\.TA$')

def _background_bond_refresh():
    """
    Fetch all TASE corporate bond instruments directly (no worker import).
    Uses yfinance EquityQuery to get all TLV instruments, filters for -BN.TA pattern.
    Bonds are priced in ILA; raw ILA value = % of par (100 ILA = 1 ILS = par).
    Stores enriched bond list in Redis under tase:bonds (1hr TTL).
    """
    # Use Redis distributed lock so only ONE fetch runs across all gunicorn workers
    from redis_client import acquire_lock as _acquire, release_lock as _release
    if not _acquire('bond_fetch', ttl=300):
        print('[App] Bond fetch lock held (Redis) — skipped.', flush=True)
        _bond_lock.release() if _bond_lock.locked() else None
        return
    if not _bond_lock.acquire(blocking=False):
        _release('bond_fetch')
        return
    try:
        print('[App] Bond fetch starting…', flush=True)
        from yfinance import EquityQuery, screen as yf_screen

        q = EquityQuery('eq', ['exchange', 'TLV'])
        all_quotes, offset = [], 0
        while True:
            try:
                res    = yf_screen(q, sortField='intradaymarketcap', sortAsc=False,
                                   offset=offset, size=100)
                quotes = res.get('quotes', [])
                if not quotes:
                    break
                all_quotes.extend(quotes)
                total = res.get('total', 0)
                offset += 100
                if offset >= total:
                    break
            except Exception as page_err:
                print(f'[App] Bond page error at offset {offset}: {page_err}', flush=True)
                break

        bonds = []
        for s in all_quotes:
            sym = s.get('symbol', '')
            m   = _BOND_PAT.match(sym)
            if not m:
                continue
            issuer_tk = m.group(1)
            series_n  = int(m.group(2))
            price_ila = s.get('regularMarketPrice')
            if not price_ila or price_ila <= 0:
                continue
            # ILA raw price = % of par (par = 1 ILS = 100 ILA per unit)
            price_pct = round(float(price_ila), 2)
            chg       = s.get('regularMarketChangePercent')
            vol       = s.get('regularMarketVolume')
            mktcap    = s.get('marketCap')
            h52       = s.get('fiftyTwoWeekHigh')
            l52       = s.get('fiftyTwoWeekLow')
            bonds.append({
                'symbol':       sym.replace('.TA', ''),
                'issuer':       issuer_tk,
                'series':       f'B{series_n}',
                'series_num':   series_n,
                'price_pct':    price_pct,
                'change_pct':   round(float(chg), 2) if chg is not None else None,
                'volume':       int(vol)    if vol    else 0,
                'market_value': int(mktcap) if mktcap else 0,
                'high52_pct':   round(float(h52), 2) if h52 else None,
                'low52_pct':    round(float(l52),  2) if l52 else None,
            })

        bonds.sort(key=lambda x: x.get('market_value') or 0, reverse=True)
        rset('tase:bonds', {'data': bonds, 'timestamp': time.time()}, ttl=3600)
        print(f'[App] Bond fetch complete — {len(bonds)} bonds cached.', flush=True)
    except Exception as e:
        print(f'[App] Bond fetch error: {e}', flush=True)
    finally:
        try: _bond_lock.release()
        except RuntimeError: pass
        try: _release('bond_fetch')
        except Exception: pass


# Bond data is fetched on-demand when the user opens the Bonds tab.
# No startup pre-fetch — avoids concurrent EquityQuery calls that
# rate-limit the worker's stock fetch on Render deployment.


def _background_refresh():
    """
    Fetch ALL TASE equities (~800 stocks) in a background thread.
    Runs in a daemon thread so it never blocks HTTP request handling.
    Takes 60-120s to complete; the frontend polls until data is ready.

    Rate-limit protection: if the last fetch failed with a Yahoo Finance
    rate-limit error, we wait 5 minutes before retrying automatically.
    Use POST /api/refresh to bypass the cooldown and force an immediate retry.
    """
    if not _refresh_lock.acquire(blocking=False):
        return
    # ── Rate-limit cooldown ─────────────────────────────────────────────
    # Don't hammer Yahoo Finance if we just got rate-limited.
    try:
        _err     = rget('tase:fetch_error') or ''
        _err_ts  = float(rget('tase:fetch_error_ts') or 0)
        if _err and _err_ts and (time.time() - _err_ts) < 300:
            print('[App] Rate-limit cooldown active — skipping refresh.', flush=True)
            _refresh_lock.release()
            return
    except Exception:
        pass
    # ── Worker lock check ───────────────────────────────────────────────
    # If the dedicated worker service is currently fetching, let it finish.
    try:
        worker_running = bool(rget('lock:fetch'))
    except Exception:
        worker_running = False
    if worker_running:
        print('[App] Worker fetch lock held — skipping background refresh.', flush=True)
        _refresh_lock.release()
        return
    try:
        print('[App] Background refresh starting (full universe)…', flush=True)
        from yfinance import EquityQuery as _EQ, screen as _screen
        import re as _re2

        _bond_pat = _re2.compile(r'-B\d|\.B\d|-P\d|\.P\d|-C\d|-M\d')

        def _pq(s):
            sym = s.get('symbol', '')
            raw = s.get('regularMarketPrice')
            if not sym or not raw or raw <= 0 or _bond_pat.search(sym):
                return None
            div   = s.get('currency', '') == 'ILA'
            price = round(raw / 100, 2) if div else round(raw, 2)
            chg   = s.get('regularMarketChangePercent')
            mc    = s.get('marketCap')
            h52   = s.get('fiftyTwoWeekHigh')
            l52   = s.get('fiftyTwoWeekLow')
            pe    = s.get('trailingPE')
            eps   = s.get('epsTrailingTwelveMonths')
            vol   = s.get('regularMarketVolume')
            div_r = s.get('trailingAnnualDividendRate')
            avg_v = s.get('averageDailyVolume3Month') or s.get('averageDailyVolume10Day')
            return {
                'ticker':      sym.replace('.TA', ''),
                'name':        s.get('longName') or s.get('shortName') or sym,
                'price':       price,
                'change_pct':  round(chg, 2) if chg is not None else None,
                'market_cap':  int(mc) if mc else None,
                'pe':          round(pe, 1) if pe and 0 < pe < 10000 else None,
                'eps':         round(eps, 2) if eps is not None else None,
                'volume':      int(vol) if vol else None,
                'week52_high': round(h52 / 100, 2) if (div and h52) else h52,
                'week52_low':  round(l52 / 100, 2) if (div and l52) else l52,
                'div_yield':   round(div_r / price * 100, 2) if (div_r and price) else None,
                'adv_ils':     int(avg_v * price) if (avg_v and price) else None,
                # yfinance screener results include sector/industry — use them directly
                # so the heatmap works without waiting for enrichment to run.
                'sector':      s.get('sector')   or '',
                'industry':    s.get('industry') or '',
            }

        _corp_bond_re = _re2.compile(r'^([A-Z0-9]+)-B(\d+)\.TA$')

        q = _EQ('eq', ['exchange', 'TLV'])
        all_q, offset = [], 0
        while True:
            try:
                res = _screen(q, sortField='intradaymarketcap', sortAsc=False,
                              offset=offset, size=100)
            except Exception as _page_err:
                print(f'[App] Page error at offset {offset}: {_page_err}', flush=True)
                break
            quotes = res.get('quotes', [])
            if not quotes:
                break
            all_q.extend(quotes)
            offset += 100
            if offset >= res.get('total', 0):
                break

        # ── Separate stocks and bonds from the same EquityQuery ──────────
        # Eliminates the duplicate bond EquityQuery that was causing rate limits.
        stocks, bond_list = [], []
        for _s in all_q:
            _sym = _s.get('symbol', '')
            _raw = _s.get('regularMarketPrice')
            if not _raw or _raw <= 0:
                continue
            _bm = _corp_bond_re.match(_sym)
            if _bm:
                # Corporate bond: price in ILA = % of par
                _chg  = _s.get('regularMarketChangePercent')
                _vol  = _s.get('regularMarketVolume')
                _mc   = _s.get('marketCap')
                _h52  = _s.get('fiftyTwoWeekHigh')
                _l52  = _s.get('fiftyTwoWeekLow')
                bond_list.append({
                    'symbol':       _sym.replace('.TA', ''),
                    'issuer':       _bm.group(1),
                    'series':       f'B{_bm.group(2)}',
                    'series_num':   int(_bm.group(2)),
                    'price_pct':    round(float(_raw), 2),
                    'change_pct':   round(float(_chg), 2) if _chg is not None else None,
                    'volume':       int(_vol)  if _vol  else 0,
                    'market_value': int(_mc)   if _mc   else 0,
                    'high52_pct':   round(float(_h52), 2) if _h52 else None,
                    'low52_pct':    round(float(_l52),  2) if _l52 else None,
                })
            elif not _bond_pat.search(_sym):
                p = _pq(_s)
                if p:
                    stocks.append(p)

        stocks.sort(key=lambda x: x.get('market_cap') or 0, reverse=True)
        bond_list.sort(key=lambda x: x.get('market_value') or 0, reverse=True)

        now_ts = time.time()
        if stocks:
            payload = {'data': stocks, 'timestamp': now_ts}
            rset('tase:stocks', payload, ttl=7200)
            rset('tase:fetch_error', None)
            rset('tase:fetch_error_ts', None)
            if not is_available():
                _cache_path = os.path.join(BASE_DIR, 'tase_cache.json')
                try:
                    with open(_cache_path, 'w', encoding='utf-8') as _f:
                        json.dump(payload, _f)
                except Exception:
                    pass
            print(f'[App] Refresh done — {len(stocks)} stocks, {len(bond_list)} bonds.',
                  flush=True)
        else:
            rset('tase:fetch_error', 'No data returned from Yahoo Finance')
            rset('tase:fetch_error_ts', now_ts)

        if bond_list:
            rset('tase:bonds', {'data': bond_list, 'timestamp': now_ts}, ttl=7200)
    except Exception as e:
        _emsg = str(e)
        print(f'[App] Background refresh error: {_emsg}', flush=True)
        rset('tase:fetch_error', _emsg)
        rset('tase:fetch_error_ts', time.time())
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
    {'label': 'TA-35',  'sym': 'TA35.TA',   'tase_id': 142},
    {'label': 'TA-125', 'sym': 'TA125.TA',  'tase_id': 137},
    {'label': 'TA-90',  'sym': 'TA90.TA',   'tase_id': 168},
    {'label': 'SME-60', 'sym': 'SME60.TA',  'tase_id': 164},
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
        # Rate-limit cooldown check — don't trigger another fetch if we just failed
        try:
            _err_ts  = float(rget('tase:fetch_error_ts') or 0)
            _in_cool = bool(error) and _err_ts and (time.time() - _err_ts) < 300
        except Exception:
            _in_cool = False
        if not _in_cool and not _refresh_lock.locked():
            threading.Thread(target=_background_refresh, daemon=True).start()
        msg = error or 'Fetching all TASE stocks… takes 60–90 s on first load.'
        return jsonify({'data': [], 'fetching': True, 'first_run': True,
                        'error': msg, 'cooldown': _in_cool})

    age = time.time() - ts if ts else None

    # Staleness check:
    # • Redis available  → use Redis TTL (< 60s remaining means stale)
    # • Redis unavailable → use file age (> 30 min means stale)
    # When Redis is down, rttl() returns -2 which is < 60, so without this
    # guard the app would trigger a background refresh on every request but
    # never persist the result, permanently looping on stale file data.
    if is_available():
        ttl_val = rttl('tase:stocks')
        stale   = bool(ttl_val is not None and ttl_val < 60)
    else:
        stale   = bool(age and age > 1800)   # stale if file is > 30 min old

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
    Clear any rate-limit error and immediately trigger a background refresh.
    Bypasses the 5-minute cooldown so the user can force a retry at any time.
    """
    rset('tase:fetch_error', None)
    rset('tase:fetch_error_ts', None)
    rset('tase:refresh_requested', True, ttl=3600)
    if not _refresh_lock.locked():
        threading.Thread(target=_background_refresh, daemon=True).start()
        return jsonify({'status': 'refresh_started',
                        'message': 'Fetching all TASE stocks now — check back in 60-90 s.'})
    return jsonify({'status': 'already_running',
                    'message': 'A refresh is already in progress.'})


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


@app.route('/api/health')
def health():
    """Lightweight liveness probe — always responds quickly."""
    data, ts = _stocks_from_redis_or_file()
    age = round(time.time() - ts) if ts else None
    uptime = round(time.time() - _APP_START)
    return jsonify({
        'ok':            True,
        'uptime_s':      uptime,
        'redis':         is_available(),
        'stock_count':   len(data) if data else 0,
        'cache_age_s':   age,
        'worker_lock':   bool(rget('lock:fetch')),
        'bond_lock':     bool(rget('lock:bond_fetch')),
        'enrich_lock':   bool(rget('lock:enrich')),
        'fetch_error':   rget('tase:fetch_error'),
        'refresh_busy':  _refresh_lock.locked(),
        'bond_busy':     _bond_lock.locked(),
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
    sym = request.args.get('sym', 'TA35.TA')
    rng = request.args.get('range', '5d')

    # Per-range TTL: intraday expires quickly; daily/weekly bars live longer
    ttl_map      = {'5d': 300, '1mo': 1800, '3mo': 3600, '6mo': 86400, '1y': 86400}
    interval_map = {'5d': '30m', '1mo': '1h', '3mo': '1d', '6mo': '1d', '1y': '1wk'}
    interval = interval_map.get(rng, '1d')
    ttl      = ttl_map.get(rng, 3600)

    cache_key = f'tase:idxhist:{sym}:{rng}'

    # ── 1. Serve from cache if still fresh ────────────────────────────────
    cached = rget(cache_key)
    if cached and cached.get('bars') and cached.get('_ts'):
        age = time.time() - cached['_ts']
        if age < ttl:
            return jsonify({'bars': cached['bars'], 'interval': cached.get('interval', interval)})

    # ── 2. Fast path: direct Yahoo Finance v8 API (~300ms vs 3-8s yfinance) ─
    bars = _fetch_chart_v8(sym, rng, interval)

    # ── 3. Fallback: yfinance wrapper (slower but more reliable for edge cases)
    if not bars:
        try:
            period_map = {'5d': '5d', '1mo': '1mo', '3mo': '3mo', '6mo': '6mo', '1y': '1y'}
            h = yf.Ticker(sym).history(period=period_map.get(rng, '1mo'), interval=interval)
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
        except Exception as e:
            # Return stale cache if available rather than empty
            if cached and cached.get('bars'):
                return jsonify({'bars': cached['bars'], 'interval': interval, 'stale': True})
            return jsonify({'bars': [], 'error': str(e)}), 500

    if bars:
        rset(cache_key, {'bars': bars, 'interval': interval, '_ts': time.time()}, ttl=ttl)
    return jsonify({'bars': bars or [], 'interval': interval})


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
    If bond cache is cold, triggers a background fetch and returns fetching=True
    so the frontend can poll again in ~60 seconds.
    """
    cached   = rget('tase:bonds')
    has_data = cached and isinstance(cached.get('data'), list) and len(cached['data']) > 0
    # Redis lock tells us if ANY gunicorn worker is already fetching
    lock_held = bool(rget('lock:bond_fetch'))

    if not has_data:
        # Bonds now come from the stock refresh (no separate bond EquityQuery needed).
        # If stocks are already loaded, trigger a stock refresh to populate bonds too.
        stocks_cached = rget('tase:stocks')
        if not stocks_cached and not _refresh_lock.locked():
            threading.Thread(target=_background_refresh, daemon=True).start()
        return jsonify({'data': [], 'bonds': [], 'timestamp': 0, 'fetching': True,
                        'message': 'Bond data populates automatically with stock refresh (~90 s).'})

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
