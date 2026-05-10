// Helpers and shared UI primitives
const { useState, useEffect, useMemo, useRef } = React;

window.fmt = {
  // Currency in ILS or USD
  money(v, ccy='ILS', d=2) {
    if (v == null || isNaN(v)) return '—';
    const sym = ccy === 'USD' ? '$' : '₪';
    if (Math.abs(v) >= 1e9) return sym + (v/1e9).toFixed(2) + 'B';
    if (Math.abs(v) >= 1e6) return sym + (v/1e6).toFixed(2) + 'M';
    if (Math.abs(v) >= 1e3) return sym + (v/1e3).toFixed(2) + 'K';
    return sym + v.toFixed(d);
  },
  num(v, d=2) {
    if (v == null || isNaN(v)) return '—';
    return Number(v).toLocaleString('en-US', {minimumFractionDigits:d, maximumFractionDigits:d});
  },
  pct(v, d=2, sign=true) {
    if (v == null || isNaN(v)) return '—';
    const s = (sign && v > 0 ? '+' : '') + v.toFixed(d) + '%';
    return s;
  },
  vol(v) {
    if (v == null) return '—';
    if (v >= 1e9) return (v/1e9).toFixed(2)+'B';
    if (v >= 1e6) return (v/1e6).toFixed(2)+'M';
    if (v >= 1e3) return (v/1e3).toFixed(0)+'K';
    return String(v);
  },
  mc(v) {
    if (v == null) return '—';
    if (v >= 1000) return '₪' + (v/1000).toFixed(2) + 'B';
    return '₪' + v.toFixed(0) + 'M';
  },
  mcShort(v) {
    if (v >= 1000) return (v/1000).toFixed(2) + 'B';
    return v.toFixed(0) + 'M';
  }
};

// Color a delta number based on sign
window.Delta = function Delta({v, suffix='%', d=2, mono=true, weight=null}) {
  if (v == null || isNaN(v)) return <span className="t-dim">—</span>;
  const cls = v > 0 ? 't-up' : v < 0 ? 't-down' : 't-dim';
  const s = (v > 0 ? '+' : '') + v.toFixed(d) + suffix;
  return <span className={cls + (mono ? ' mono' : '')} style={weight ? {fontWeight:weight} : null}>{s}</span>;
};

// Mini sparkline
window.Spark = function Spark({data, color, width=60, height=18}) {
  if (!data || data.length < 2) return null;
  const min = Math.min(...data), max = Math.max(...data);
  const range = max - min || 1;
  const pts = data.map((v,i) => `${(i/(data.length-1))*width},${height - ((v-min)/range)*height}`).join(' ');
  return (
    <svg width={width} height={height} className="spark">
      <polyline points={pts} fill="none" stroke={color || 'var(--accent)'} strokeWidth="1" vectorEffect="non-scaling-stroke" />
    </svg>
  );
};

// Generate a fake price series from change and seed
window.makeSeries = function(seed, ch, n=40) {
  const out = [];
  let v = 100;
  for (let i = 0; i < n; i++) {
    const t = i/(n-1);
    const noise = Math.sin(seed*1.3 + i*0.7) * 0.6 + Math.cos(seed*0.8 + i*1.4) * 0.4;
    v = 100 + ch * t + noise;
    out.push(v);
  }
  return out;
};

// Candlesticks - generate fake OHLC data
window.makeCandles = function(seed, n=40, trend=0) {
  const out = [];
  let last = 100;
  for (let i = 0; i < n; i++) {
    const open = last;
    const drift = trend / n;
    const noise = (Math.sin(seed*1.7 + i*0.9) + Math.cos(seed*0.5 + i*1.1)) * 0.8;
    const close = open + drift + noise;
    const high = Math.max(open, close) + Math.abs(noise)*0.5 + 0.2;
    const low = Math.min(open, close) - Math.abs(noise)*0.5 - 0.2;
    out.push({open, high, low, close});
    last = close;
  }
  return out;
};

// Generic small components
window.Pill = function Pill({children, tone, onClick, active}) {
  const cls = ['pill', tone && `pill-${tone}`, active && 'is-active', onClick && 'is-clickable'].filter(Boolean).join(' ');
  return <span className={cls} onClick={onClick}>{children}</span>;
};

window.Hebrew = function Hebrew({children}) {
  return <span dir="rtl" lang="he" style={{unicodeBidi:'isolate'}}>{children}</span>;
};
window.LTR = function LTR({children, className}) {
  return <span dir="ltr" style={{unicodeBidi:'isolate'}} className={className}>{children}</span>;
};

// Color helpers for heatmap intensity — discrete 9-step scale for legibility
// Buckets:  <=-3   -3..-2   -2..-1   -1..-0.2   -0.2..+0.2   +0.2..+1   +1..+2   +2..+3   >=+3
window.heatColor = function(ch) {
  if (ch <= -3)   return '#7a1418';
  if (ch <= -2)   return '#9a1c1f';
  if (ch <= -1)   return '#b32a2d';
  if (ch <= -0.2) return '#5a2528';
  if (ch <   0.2) return '#26282d';
  if (ch <   1)   return '#1f4a2a';
  if (ch <   2)   return '#1f6a34';
  if (ch <   3)   return '#1f8a3e';
  return '#1faa4a';
};
// Text color depending on background intensity
window.heatText = function(ch) {
  return Math.abs(ch) < 0.2 ? '#a8acb3' : '#ffffff';
};
