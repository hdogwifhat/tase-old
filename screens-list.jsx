// Stock List screen — the main screener table
window.ScreenList = function ScreenList({ccy, setRoute, setSelectedTicker}) {
  const D = window.TASE_DATA;
  const ilsPerUsd = D._fx?.ils_per_usd || 3.65;
  const [sortKey, setSortKey] = useState('mc');
  const [sortDir, setSortDir] = useState('desc');
  const [favs, setFavs] = useState(new Set());

  const toggleFav = (t) => {
    setFavs(prev => {
      const n = new Set(prev);
      n.has(t) ? n.delete(t) : n.add(t);
      return n;
    });
  };

  const sorted = useMemo(() => {
    const arr = [...D.stocks];
    arr.sort((a,b) => {
      const av = a[sortKey], bv = b[sortKey];
      if (av == null) return 1; if (bv == null) return -1;
      return sortDir === 'desc' ? bv - av : av - bv;
    });
    return arr;
  }, [sortKey, sortDir]);

  const cols = [
    {k:'fav', l:'', w:24, c:'c-fav'},
    {k:'t', l:'TICKER', w:72, c:'c-tick', sortable:false},
    {k:'n', l:'COMPANY', w:'1.6fr', c:'c-name', sortable:false},
    {k:'p', l:'PRICE', w:90, c:'c-num', num:true},
    {k:'ch', l:'CHG %', w:80, c:'c-num', num:true},
    {k:'spark', l:'5D', w:64, c:'c-spark'},
    {k:'mc', l:'MKT CAP', w:96, c:'c-num', num:true},
    {k:'pe', l:'P/E', w:64, c:'c-num', num:true},
    {k:'vol', l:'VOLUME', w:80, c:'c-num', num:true},
    {k:'div', l:'DIV %', w:64, c:'c-num', num:true},
    {k:'roe', l:'ROE %', w:72, c:'c-num', num:true},
    {k:'rev', l:'REV GR', w:80, c:'c-num', num:true},
    {k:'beta', l:'β', w:54, c:'c-num', num:true},
    {k:'fpe', l:'FWD P/E', w:80, c:'c-num', num:true},
    {k:'ps', l:'P/S', w:60, c:'c-num', num:true},
    {k:'ev', l:'EV/EBITDA', w:90, c:'c-num', num:true},
  ];

  const sortBy = (k) => {
    if (sortKey === k) setSortDir(d => d === 'desc' ? 'asc' : 'desc');
    else { setSortKey(k); setSortDir('desc'); }
  };

  const gridCols = cols.map(c => typeof c.w === 'number' ? c.w + 'px' : c.w).join(' ');

  return (
    <div className="screen screen-list" data-screen-label="02 Stock List">
      <div className="list-h">
        <div className="bc"><span className="bc-c">Home</span><span className="bc-sep">›</span><span>All equities</span></div>
        <div className="list-meta"><span className="mono">{sorted.length}</span> rows · sorted by <span className="mono">{cols.find(c=>c.k===sortKey)?.l}</span></div>
        <div className="grow"/>
        <button className="btn btn-ghost btn-sm">↓ CSV</button>
      </div>

      <div className="tbl-wrap">
        <div className="tbl mono-tbl" style={{['--cols']: gridCols}}>
          <div className="tr tr-h">
            {cols.map(c => (
              <div key={c.k} className={'th ' + c.c + (sortKey===c.k ? ' is-sort' : '')} onClick={c.sortable===false ? null : ()=>sortBy(c.k)}>
                <span>{c.l}</span>
                {sortKey===c.k && <span className="sort-i">{sortDir==='desc' ? '▼' : '▲'}</span>}
              </div>
            ))}
          </div>
          {sorted.map((s, i) => (
            <div key={s.t} className={'tr ' + (i%2 ? 'tr-z' : '')} onClick={()=>{ setSelectedTicker(s.t); setRoute('detail'); }}>
              <div className="td c-fav" onClick={e=>{e.stopPropagation(); toggleFav(s.t);}}>
                <span className={'star ' + (favs.has(s.t) ? 'is-on' : '')}>{favs.has(s.t) ? '★' : '☆'}</span>
              </div>
              <div className="td c-tick mono">{s.t}</div>
              <div className="td c-name">{s.n}</div>
              <div className="td c-num mono">{ccy === 'ILS' ? '₪' : '$'}{fmt.num(ccy==='ILS'?s.p:(s.p/ilsPerUsd), 2)}</div>
              <div className="td c-num mono"><Delta v={s.ch} d={2}/></div>
              <div className="td c-spark"><Spark data={makeSeries(s.t.charCodeAt(0)+s.t.charCodeAt(1), s.ch*4)} color={s.ch>=0?'var(--up)':'var(--down)'} width={56} height={16}/></div>
              <div className="td c-num mono">{fmt.mc(s.mc)}</div>
              <div className="td c-num mono">{s.pe!=null ? s.pe.toFixed(1)+'x' : '—'}</div>
              <div className="td c-num mono t-dim">{fmt.vol(s.vol)}</div>
              <div className="td c-num mono">{s.div!=null ? s.div.toFixed(1)+'%' : <span className="t-dim">—</span>}</div>
              <div className="td c-num mono"><Delta v={s.roe} d={1}/></div>
              <div className="td c-num mono"><Delta v={s.rev} d={1}/></div>
              <div className="td c-num mono">{s.beta?.toFixed(2) ?? '—'}</div>
              <div className="td c-num mono">{s.fpe!=null ? s.fpe.toFixed(1)+'x' : '—'}</div>
              <div className="td c-num mono">{s.ps?.toFixed(1) ?? '—'}</div>
              <div className="td c-num mono">{s.ev!=null ? s.ev.toFixed(1)+'x' : <span className="t-dim">—</span>}</div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
};

// Heatmap screen — full treemap with two views, legend, breadth, click-through
window.ScreenHeat = function ScreenHeat() {
  const D = window.TASE_DATA;
  const [view, setView] = useState('sector'); // 'sector' | 'flat'
  const [universe, setUniverse] = useState('TA-125');
  const [size, setSize] = useState('mcap'); // 'mcap' | 'equal'

  // Build a flat list of {t, name, sec, mc, ch, price}
  const flat = useMemo(() => {
    return D.stocks.map(s => ({
      t: s.t, name: s.n, sec: s.s, mc: s.mc, ch: s.ch, price: s.p
    })).sort((a,b) => b.mc - a.mc);
  }, []);

  // Breadth across universe
  const breadth = useMemo(() => {
    const up = flat.filter(x => x.ch > 0.05).length;
    const dn = flat.filter(x => x.ch < -0.05).length;
    const fl = flat.length - up - dn;
    const upMc = flat.filter(x => x.ch > 0.05).reduce((a,b)=>a+b.mc,0);
    const dnMc = flat.filter(x => x.ch < -0.05).reduce((a,b)=>a+b.mc,0);
    const totalMc = flat.reduce((a,b)=>a+b.mc,0);
    const wAvg = flat.reduce((a,b)=>a + b.ch * b.mc, 0) / totalMc;
    return { up, dn, fl, upMc, dnMc, totalMc, wAvg };
  }, [flat]);

  // Sectors sorted by total mcap, items inside sorted by mcap
  const sectors = useMemo(() => {
    return D.heatmap
      .map(g => ({...g, items: [...g.items].sort((a,b)=>b.mc-a.mc)}))
      .sort((a,b) => b.total - a.total);
  }, []);

  const onCell = (t) => { window.location.hash = '#detail/' + t; };

  return (
    <div className="screen screen-heat" data-screen-label="03 Heatmap">
      {/* Header: title + breadth summary + controls */}
      <div className="heat-h2">
        <div className="heat-h2-l">
          <h1 className="heat-title">Market Heatmap</h1>
          <div className="heat-sub">
            <span className="t-dim">Cap-weighted Δ</span>
            <span className={'mono ' + (breadth.wAvg >= 0 ? 't-up' : 't-down')}>
              <Delta v={breadth.wAvg} d={2}/>
            </span>
            <span className="heat-pipe"/>
            <span className="t-dim">Breadth</span>
            <span className="mono"><span className="t-up">▲{breadth.up}</span> <span className="t-dim">·</span> <span className="t-flat">●{breadth.fl}</span> <span className="t-dim">·</span> <span className="t-down">▼{breadth.dn}</span></span>
            <span className="heat-pipe"/>
            <span className="t-dim">Up mcap share</span>
            <span className="mono">{((breadth.upMc/breadth.totalMc)*100).toFixed(0)}%</span>
          </div>
        </div>
        <div className="grow"/>
        <div className="heat-ctrls">
          <div className="seg">
            <button className={'seg-b ' + (universe==='TA-35'?'on':'')} onClick={()=>setUniverse('TA-35')}>TA-35</button>
            <button className={'seg-b ' + (universe==='TA-90'?'on':'')} onClick={()=>setUniverse('TA-90')}>TA-90</button>
            <button className={'seg-b ' + (universe==='TA-125'?'on':'')} onClick={()=>setUniverse('TA-125')}>TA-125</button>
          </div>
          <div className="seg">
            <button className={'seg-b ' + (view==='sector'?'on':'')} onClick={()=>setView('sector')}>By sector</button>
            <button className={'seg-b ' + (view==='flat'?'on':'')} onClick={()=>setView('flat')}>Flat treemap</button>
          </div>
          <div className="seg">
            <button className={'seg-b ' + (size==='mcap'?'on':'')} onClick={()=>setSize('mcap')}>Size: mcap</button>
            <button className={'seg-b ' + (size==='equal'?'on':'')} onClick={()=>setSize('equal')}>Size: equal</button>
          </div>
        </div>
      </div>

      {/* Color legend */}
      <div className="heat-legend">
        <span className="hl-l">Daily change</span>
        <div className="hl-scale">
          {[-3.5,-2.5,-1.5,-0.6,0,0.6,1.5,2.5,3.5].map((v,i) => (
            <div key={i} className="hl-cell" style={{background: heatColor(v)}}/>
          ))}
        </div>
        <div className="hl-ticks mono">
          <span>≤ −3%</span><span>−2%</span><span>−1%</span><span>0%</span><span>+1%</span><span>+2%</span><span>≥ +3%</span>
        </div>
      </div>

      {/* Body */}
      {view === 'sector' ? (
        <div className="heat-board2">
          {sectors.map((g) => {
            // Span based on real mcap weight
            const w = g.total / sectors[0].total; // 0..1 vs largest
            const colSpan = Math.max(3, Math.min(6, Math.round(3 + w*3)));
            const rowSpan = w > 0.6 ? 2 : 1;
            const upN = g.items.filter(x=>x.ch>0.05).length;
            const dnN = g.items.filter(x=>x.ch<-0.05).length;
            return (
              <section key={g.sec} className="hsec" style={{gridColumn:`span ${colSpan}`, gridRow:`span ${rowSpan}`}}>
                <header className="hsec-h">
                  <div className="hsec-h-l">
                    <span className="hsec-n">{g.sec}</span>
                    <span className="hsec-he t-dim" dir="rtl">{g.he}</span>
                  </div>
                  <div className="hsec-h-r mono">
                    <span className={g.avgCh>=0 ? 't-up' : 't-down'}><Delta v={g.avgCh} d={2}/></span>
                    <span className="t-dim">·</span>
                    <span className="t-dim">{g.items.length}</span>
                    <span className="t-dim">·</span>
                    <span><span className="t-up">▲{upN}</span> <span className="t-down">▼{dnN}</span></span>
                  </div>
                </header>
                <div className="hsec-body">
                  {g.items.map((it, ii) => (
                    <button
                      key={it.t+ii}
                      className="hcell"
                      onClick={()=>onCell(it.t)}
                      style={{
                        background: heatColor(it.ch),
                        color: heatText(it.ch),
                        flexGrow: size==='mcap' ? Math.max(0.5, Math.sqrt(it.mc / 1000)) : 1,
                        flexShrink: 1,
                        flexBasis: '64px',
                      }}
                      title={`${it.t} · ${it.ch.toFixed(2)}% · mcap ₪${(it.mc/1000).toFixed(1)}B`}
                    >
                      <span className="hcell-t mono">{it.t}</span>
                      <span className="hcell-c mono">{it.ch>=0?'+':''}{it.ch.toFixed(2)}%</span>
                    </button>
                  ))}
                </div>
              </section>
            );
          })}
        </div>
      ) : (
        <div className="heat-flat">
          {flat.map((it, i) => (
            <button
              key={it.t+i}
              className="hcell hcell-flat"
              onClick={()=>onCell(it.t)}
              style={{
                background: heatColor(it.ch),
                color: heatText(it.ch),
                flexGrow: size==='mcap' ? Math.max(0.5, Math.sqrt(it.mc / 1000)) : 1,
                flexShrink: 1,
                flexBasis: '80px',
              }}
              title={`${it.t} · ${it.name} · ${it.ch.toFixed(2)}% · ₪${it.price.toFixed(2)} · mcap ₪${(it.mc/1000).toFixed(1)}B`}
            >
              <span className="hcell-t mono">{it.t}</span>
              <span className="hcell-c mono">{it.ch>=0?'+':''}{it.ch.toFixed(2)}%</span>
              <span className="hcell-mc mono t-dim2">₪{it.mc>=10000 ? (it.mc/1000).toFixed(0)+'B' : (it.mc/1000).toFixed(1)+'B'}</span>
            </button>
          ))}
        </div>
      )}

      <div className="heat-foot t-dim">
        Click any tile to open the stock. Tile size = market cap. Color = today's % change.
      </div>
    </div>
  );
};
