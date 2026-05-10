// Home dashboard
window.ScreenHome = function ScreenHome({ccy, setRoute, setSelectedTicker}) {
  const D = window.TASE_DATA;

  // Compute live KPIs from real stock data
  const stocks = D.stocks;
  const totalMc  = stocks.reduce((a, s) => a + (s.mc || 0), 0); // millions ILS
  const gainers  = stocks.filter(s => (s.ch || 0) > 0).length;
  const losers   = stocks.filter(s => (s.ch || 0) < 0).length;
  const total    = stocks.length;
  const breadth  = losers > 0 ? (gainers / losers).toFixed(2) : '—';
  const pes      = stocks.map(s => s.pe).filter(v => v != null && v > 0 && v < 500).sort((a,b) => a - b);
  const medPE    = pes.length ? pes[Math.floor(pes.length / 2)] : null;
  const roes     = stocks.map(s => s.roe).filter(v => v != null).sort((a,b) => a - b);
  const medROE   = roes.length ? roes[Math.floor(roes.length / 2)] : null;
  const highs    = stocks.filter(s => s.w52h && s.p && s.p >= s.w52h * 0.99).length;
  const lows52   = stocks.filter(s => s.w52l && s.p && s.p <= s.w52l * 1.01).length;

  return (
    <div className="screen screen-home" data-screen-label="01 Home">
      {/* KPI row */}
      <div className="kpi-row">
        <div className="kpi">
          <div className="kpi-l">SCREENED MARKET CAP</div>
          <div className="kpi-v mono">
            <span className="kpi-sym">₪</span>
            {totalMc >= 1000 ? (totalMc/1000).toFixed(2) : totalMc.toFixed(0)}
            <span className="kpi-u">{totalMc >= 1000 ? 'T' : 'B'}</span>
          </div>
          <div className="kpi-s">{total} names in current screen</div>
        </div>
        <div className="kpi">
          <div className="kpi-l">GAINERS &amp; LOSERS</div>
          <div className="kpi-v mono">
            <span className="t-up">{gainers}</span>
            <span className="t-dim"> / </span>
            <span className="t-down">{losers}</span>
          </div>
          <div className="kpi-s">{total > 0 ? Math.round(gainers/total*100) : 0}% positive · breadth {breadth}</div>
        </div>
        <div className="kpi">
          <div className="kpi-l">MEDIAN P/E</div>
          <div className="kpi-v mono">{medPE != null ? medPE.toFixed(1) : '—'}<span className="kpi-u">x</span></div>
          <div className="kpi-s">{pes.length} companies with P/E</div>
        </div>
        <div className="kpi">
          <div className="kpi-l">MEDIAN ROE</div>
          <div className="kpi-v mono">{medROE != null ? medROE.toFixed(1) : '—'}<span className="kpi-u">%</span></div>
          <div className="kpi-s">Profitability pulse</div>
        </div>
        <div className="kpi">
          <div className="kpi-l">52-WK NEW HIGHS</div>
          <div className="kpi-v mono">
            {highs} <span className="kpi-u t-dim">/</span> <span className="t-down" style={{fontSize:'inherit'}}>{lows52}</span>
          </div>
          <div className="kpi-s">vs new lows · last session</div>
        </div>
      </div>

      <div className="home-grid">
        {/* Today's movers */}
        <div className="panel">
          <div className="panel-h">
            <div className="panel-t">TODAY'S MOVERS</div>
            <div className="panel-s">Top 5 gainers &amp; losers</div>
          </div>
          <div className="movers">
            <div className="movers-col">
              <div className="movers-h"><span className="t-up">▲ GAINERS</span></div>
              {D.movers.map(m => (
                <div key={m.t} className="mover" onClick={()=>{ setSelectedTicker(m.t); setRoute('detail'); }}>
                  <span className="mover-t mono">{m.t}</span>
                  <span className="mover-n">{m.n}</span>
                  <span className="mover-c mono"><Delta v={m.ch} d={2} /></span>
                </div>
              ))}
            </div>
            <div className="movers-col">
              <div className="movers-h"><span className="t-down">▼ LOSERS</span></div>
              {D.losers.map(m => (
                <div key={m.t} className="mover">
                  <span className="mover-t mono">{m.t}</span>
                  <span className="mover-n">{m.n}</span>
                  <span className="mover-c mono"><Delta v={m.ch} d={2} /></span>
                </div>
              ))}
            </div>
          </div>
        </div>

        {/* Index chart */}
        <div className="panel panel-chart">
          <div className="panel-h">
            <div className="panel-t">INDEX CHART</div>
            <div className="panel-s">TA-35 · candlesticks</div>
            <div className="grow"/>
            <div className="seg seg-sm">
              {['TA-35','TA-90'].map(ix => <button key={ix} className={'seg-btn ' + (ix==='TA-35' && 'is-active')}>{ix}</button>)}
            </div>
            <div className="seg seg-sm">
              {['5D','1M','3M','6M','1Y'].map(p => <button key={p} className={'seg-btn ' + (p==='5D' && 'is-active')}>{p}</button>)}
            </div>
          </div>
          <div className="chart-wrap">
            <CandleChart seed={4} bars={48} trend={12} height={200} />
          </div>
          <div className="chart-foot">
            <span className="mono t-up">5D · +1.59%</span>
            <span className="t-dim">·</span>
            <span className="mono">last 4514.42</span>
            <span className="t-dim">·</span>
            <span className="mono">range 4480.10 – 4540.20</span>
          </div>
        </div>

        {/* Heatmap mini */}
        <div className="panel panel-heat">
          <div className="panel-h">
            <div className="panel-t">MARKET HEATMAP</div>
            <div className="panel-s">1 block = 1 sector · size = mcap · color = daily Δ</div>
            <div className="grow"/>
            <button className="link-btn" onClick={()=>setRoute('heat')}>expand →</button>
          </div>
          <div className="mini-heat">
            {D.heatmap.slice(0,12).map(g => (
              <div key={g.sec} className="mh-cell" style={{background: heatColor(g.avgCh), gridColumn: `span ${Math.max(1, Math.min(4, Math.round(g.total/15000)))}`}}>
                <div className="mh-name">{g.sec}</div>
                <div className="mh-ch mono"><Delta v={g.avgCh} d={1} /></div>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
};

// Candlestick chart
window.CandleChart = function CandleChart({seed=1, bars=40, trend=0, height=180}) {
  const data = useMemo(()=>makeCandles(seed,bars,trend),[seed,bars,trend]);
  const W = 800, H = height;
  const padR = 50, padL = 8;
  const inner = W - padR - padL;
  const all = data.flatMap(d => [d.high, d.low]);
  const max = Math.max(...all), min = Math.min(...all);
  const range = max - min;
  const bw = inner / data.length;
  const yScale = v => 8 + ((max - v) / range) * (H - 16);
  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="cchart" preserveAspectRatio="none">
      {/* Horizontal grid */}
      {[0.25, 0.5, 0.75].map(t => (
        <line key={t} x1={padL} x2={W-padR} y1={8 + t*(H-16)} y2={8 + t*(H-16)} stroke="var(--bd)" strokeDasharray="2 4" strokeWidth="0.5"/>
      ))}
      {data.map((c, i) => {
        const x = padL + i*bw + bw/2;
        const up = c.close >= c.open;
        const col = up ? 'var(--up)' : 'var(--down)';
        return (
          <g key={i}>
            <line x1={x} x2={x} y1={yScale(c.high)} y2={yScale(c.low)} stroke={col} strokeWidth="1"/>
            <rect x={x - bw*0.32} y={yScale(Math.max(c.open,c.close))} width={bw*0.64} height={Math.max(1,Math.abs(yScale(c.open)-yScale(c.close)))} fill={col}/>
          </g>
        );
      })}
      {/* Last price tag */}
      <line x1={padL} x2={W-padR} y1={yScale(data[data.length-1].close)} y2={yScale(data[data.length-1].close)} stroke="var(--accent)" strokeDasharray="3 3" strokeWidth="0.6"/>
      <rect x={W-padR+2} y={yScale(data[data.length-1].close)-7} width={padR-4} height={14} fill="var(--accent)"/>
      <text x={W-padR/2} y={yScale(data[data.length-1].close)+3} fill="#000" fontSize="10" textAnchor="middle" fontFamily="ui-monospace,monospace" fontWeight="600">{data[data.length-1].close.toFixed(2)}</text>
      {/* Y-axis labels */}
      {[max, (max+min)/2, min].map((v,i)=>(
        <text key={i} x={W-padR+4} y={[14,H/2+3,H-4][i]} fill="var(--t3)" fontSize="9" fontFamily="ui-monospace,monospace">{v.toFixed(2)}</text>
      ))}
    </svg>
  );
};
