// data.jsx — fetches live data from Flask API and populates window.TASE_DATA
// Field mapping: Flask snake_case → Claude Design short keys

// ── Hebrew sector labels ───────────────────────────────────────────────────
const _SECTOR_HE = {
  'Technology':           'טכנולוגיה',
  'Financial Services':   'בנקים',
  'Real Estate':          'נדל"ן',
  'Industrials':          'תעשייה',
  'Healthcare':           'ביוטק',
  'Energy':               'אנרגיה',
  'Utilities':            'תשתיות',
  'Consumer Cyclical':    'מסחר',
  'Consumer Defensive':   'מזון',
  'Basic Materials':      'חומרי גלם',
  'Communication Services':'תקשורת',
  'Communication':        'תקשורת',
  'Other':                'אחר',
};

// ── Initial empty state (shown while loading) ─────────────────────────────
window.TASE_DATA = {
  _loading:   true,
  _fetching:  false,
  _error:     null,
  _cacheAge:  null,
  _count:     0,
  _fx:        { ils_per_usd: 3.65, usd_per_ils: 0.274, change_pct: null },
  stocks:     [],
  indices:    [],
  sectorPerf: [],
  heatmap:    [],
  movers:     [],
  losers:     [],
  bonds:      [],
};

// ── Field mapping helpers ─────────────────────────────────────────────────

function _mapStock(s) {
  // market_cap from yfinance is in ILA (agorot) → divide by 1e8 for millions ILS
  // BUT some enrichment paths may store it in ILS already — we normalise by checking:
  // if mc > 1e9 it's likely in ILA; otherwise assume ILS already in millions.
  const rawMc = s.market_cap || 0;
  const mc = rawMc > 1e9
    ? Math.round(rawMc / 1e8)   // ILA → millions ILS
    : Math.round(rawMc / 1e6);  // assume raw ILS → millions ILS (just in case)

  return {
    t:       s.ticker,
    n:       s.name,
    he:      _SECTOR_HE[s.sector] ? s.name : s.name, // no Hebrew company names from API
    s:       s.sector   || 'Other',
    p:       s.price,
    ch:      s.change_pct,
    mc:      mc || null,
    pe:      s.pe,
    vol:     s.volume,
    div:     s.div_yield,
    roe:     s.roe,
    rev:     s.revenue_growth,
    ev:      s.ev_ebitda,
    beta:    s.beta,
    eps:     s.eps,
    fpe:     s.forward_pe,
    ps:      s.ps_ratio,
    pb:      s.pb_ratio,
    evfcf:   s.ev_fcf,
    opm:     s.op_margin,
    gm:      s.gross_margin,
    nm:      s.net_margin,
    dscr:    s.debt_equity,
    qr:      s.quick_ratio,
    w52h:    s.week52_high,
    w52l:    s.week52_low,
    arb_pct: s.arb_pct,
    us_ticker: s.us_ticker,
    us_price:  s.us_price,
  };
}

function _mapBond(b) {
  const range = (b.low52_pct != null && b.high52_pct != null)
    ? `${b.low52_pct.toFixed(2)} – ${b.high52_pct.toFixed(2)}`
    : '—';
  return {
    t:    b.issuer || (b.symbol || '').split('-')[0],
    n:    b.issuer_name || b.issuer || '—',
    ser:  b.series || '—',
    p:    b.price_pct,
    ch:   b.change_pct,
    ytm:  b.ytm || null,
    vol:  b.volume,
    ad:   b.adv_ils ? b.adv_ils / 1e6 : null,
    range,
    mod:  null,
    dur:  null,
    cpn:  b.coupon_rate || null,
    ytw:  null,
    rt:   b.rating || null,
    sec2: b.sector || null,
  };
}

function _buildHeatmap(stocks) {
  const groups = {};
  for (const s of stocks) {
    const sec = s.s || 'Other';
    if (!groups[sec]) groups[sec] = { sec, he: _SECTOR_HE[sec] || sec, items: [], total: 0 };
    if (s.mc) {
      groups[sec].items.push({ t: s.t, ch: s.ch || 0, mc: s.mc });
      groups[sec].total += s.mc;
    }
  }
  for (const g of Object.values(groups)) {
    const totalMc = g.total;
    g.avgCh = totalMc > 0
      ? g.items.reduce((a, b) => a + (b.ch || 0) * (b.mc || 0), 0) / totalMc
      : 0;
    g.avgCh = Math.round(g.avgCh * 100) / 100;
    g.items.sort((a, b) => (b.mc || 0) - (a.mc || 0));
  }
  return Object.values(groups)
    .filter(g => g.items.length > 0)
    .sort((a, b) => b.total - a.total);
}

function _buildMovers(stocks, n = 5) {
  const valid = stocks.filter(s => s.ch != null && s.p > 0.1);
  const sorted = [...valid].sort((a, b) => b.ch - a.ch);
  return sorted.slice(0, n).map(s => ({ t: s.t, n: s.n, ch: s.ch }));
}

function _buildLosers(stocks, n = 5) {
  const valid = stocks.filter(s => s.ch != null && s.p > 0.1);
  const sorted = [...valid].sort((a, b) => a.ch - b.ch);
  return sorted.slice(0, n).map(s => ({ t: s.t, n: s.n, ch: s.ch }));
}

// ── Polling logic ─────────────────────────────────────────────────────────

let _pollTimer = null;

async function _fetchAndPopulate() {
  try {
    // Fetch stocks + indices + fx + bonds concurrently
    const [stocksRes, indicesRes, fxRes, bondsRes] = await Promise.allSettled([
      fetch('/api/stocks').then(r => r.json()),
      fetch('/api/indices').then(r => r.json()),
      fetch('/api/fx').then(r => r.json()),
      fetch('/api/bonds').then(r => r.json()),
    ]);

    const stocksJson  = stocksRes.status  === 'fulfilled' ? stocksRes.value  : {};
    const indicesJson = indicesRes.status === 'fulfilled' ? indicesRes.value : {};
    const fxJson      = fxRes.status      === 'fulfilled' ? fxRes.value      : {};
    const bondsJson   = bondsRes.status   === 'fulfilled' ? bondsRes.value   : {};

    const rawStocks  = stocksJson.data   || [];
    const isFetching = stocksJson.fetching === true;
    const cacheAge   = stocksJson.cache_age || null;
    const error      = stocksJson.error   || null;

    const mappedStocks  = rawStocks.map(_mapStock);
    const mappedBonds   = (bondsJson.bonds || []).map(_mapBond);

    // Indices: [{label, sym, tase_id, price, change_pct}]
    const mappedIndices = (indicesJson.main || []).map(ix => ({
      t:  ix.label,
      v:  ix.price,
      ch: ix.change_pct,
    }));

    // Sector performance from /api/indices: [{label, price, change_pct}]
    const mappedSectorPerf = (indicesJson.sectors || []).map(sp => ({
      s:  sp.label,      // Hebrew
      ch: sp.change_pct,
    }));

    window.TASE_DATA = {
      _loading:   isFetching && rawStocks.length === 0,
      _fetching:  isFetching,
      _error:     error,
      _cacheAge:  cacheAge,
      _count:     rawStocks.length,
      _fx:        { ils_per_usd: fxJson.ils_per_usd || 3.65, usd_per_ils: fxJson.usd_per_ils || 0.274, change_pct: fxJson.change_pct || null },
      stocks:     mappedStocks,
      indices:    mappedIndices.length ? mappedIndices : [
        { t: 'TA-35', v: null, ch: null },
        { t: 'TA-125', v: null, ch: null },
        { t: 'TA-90', v: null, ch: null },
        { t: 'SME-60', v: null, ch: null },
      ],
      sectorPerf: mappedSectorPerf,
      heatmap:    _buildHeatmap(mappedStocks),
      movers:     _buildMovers(mappedStocks),
      losers:     _buildLosers(mappedStocks),
      bonds:      mappedBonds,
    };

    // Notify React
    if (window._refreshReact) window._refreshReact();

    // If still fetching, poll again in 15 s
    if (isFetching || rawStocks.length === 0) {
      clearTimeout(_pollTimer);
      _pollTimer = setTimeout(_fetchAndPopulate, 15000);
    }

  } catch (err) {
    console.error('[TASE data] fetch error:', err);
    window.TASE_DATA = { ...window.TASE_DATA, _loading: false, _error: String(err) };
    if (window._refreshReact) window._refreshReact();
    // Retry in 30 s on network error
    clearTimeout(_pollTimer);
    _pollTimer = setTimeout(_fetchAndPopulate, 30000);
  }
}

// Expose for manual retry button in app.jsx
window._fetchAndPopulate = _fetchAndPopulate;

// Kick off immediately when scripts load
_fetchAndPopulate();
