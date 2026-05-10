// Top chrome: brand, breadcrumbs, status, ribbon nav, indices strip, sub-strip, filters
const { useState: useStateChrome } = React;

window.TopChrome = function TopChrome({route, setRoute, ccy, setCcy, density, setDensity}) {
  const D = window.TASE_DATA;
  const fx = D._fx || {};
  const fxRate = fx.ils_per_usd ? fx.ils_per_usd.toFixed(4) : '—';
  const fxCh   = fx.change_pct;
  const count  = D._count || D.stocks.length || 0;
  const age    = D._cacheAge;

  // Format cache age label
  const ageLabel = age == null ? '…' : age < 60 ? `${age}s ago` : `${Math.round(age/60)}m ago`;

  const handleRefresh = () => {
    fetch('/api/refresh', {method:'POST'})
      .then(() => {
        setTimeout(() => window._fetchAndPopulate && window._fetchAndPopulate(), 3000);
      })
      .catch(() => {});
  };

  return (
    <div className="chrome">
      <div className="bar bar-1">
        <div className="brand">
          <div className="brand-mark">TA</div>
          <div className="brand-name">TASE Screener</div>
          <div className="brand-sub">Tel Aviv Stock Exchange</div>
        </div>
        <div className="bar-sep" />
        <Pill tone="info">Cached · {ageLabel}</Pill>
        <Pill>{count} stocks</Pill>
        <div className="grow" />
        <div className="seg">
          <button className={'seg-btn ' + (ccy==='ILS' && 'is-active')} onClick={()=>setCcy('ILS')}>ILS</button>
          <button className={'seg-btn ' + (ccy==='USD' && 'is-active')} onClick={()=>setCcy('USD')}>USD</button>
        </div>
        <button className={"btn btn-ghost " + (route==='dcf' && 'is-active')} onClick={()=>setRoute('dcf')}><span className="kbd-icon">∫</span> DCF</button>
        <button className="btn btn-ghost">≡ Columns</button>
        <button className="btn btn-primary" onClick={handleRefresh}>↻ Refresh</button>
      </div>

      <nav className="bar bar-2">
        {[
          ['home','Home'],['list','Stock List'],['heat','Heatmap'],['short','Shortlist'],
          ['sect','Sectors'],['val','Value'],['qual','Quality'],['breadth','Breadth'],['fav','★ Favorites']
        ].map(([k,l]) => (
          <button key={k} className={'tab ' + (route===k ? 'is-active' : '')} onClick={()=>setRoute(k)}>{l}</button>
        ))}
        <button className={'tab tab-bonds ' + (route==='bonds' ? 'is-active' : '')} onClick={()=>setRoute('bonds')}><Hebrew>אג"ח</Hebrew> Bonds</button>
      </nav>

      {/* Indices strip */}
      <div className="indices">
        {D.indices.map(ix => (
          <div key={ix.t} className="idx">
            <div className="idx-t">{ix.t}</div>
            <div className="idx-v mono">{ix.v != null ? fmt.num(ix.v) : '—'}</div>
            <div className="idx-c"><Delta v={ix.ch} d={2} /></div>
          </div>
        ))}
        <div className="idx-sep" />
        {D.sectorPerf.map(sp => (
          <div key={sp.s} className="idx idx-sect">
            <div className="idx-t" dir="rtl">{sp.s}</div>
            <div className="idx-c"><Delta v={sp.ch} d={2} /></div>
          </div>
        ))}
        <div className="grow" />
        <div className="idx idx-fx">
          <div className="idx-t">USD/ILS</div>
          <div className="idx-v mono">{fxRate}</div>
          {fxCh != null && <div className="idx-c"><Delta v={fxCh} d={3} /></div>}
        </div>
      </div>

      {/* Compact sub-strip */}
      <div className="substrip">
        <span className="ss-k">USD/ILS</span>
        <span className="mono ss-v">{fxRate}</span>
        {fxCh != null && <span className="ss-d"><Delta v={fxCh} d={3}/></span>}
        <span className="ss-k">BREADTH</span>
        <span className={'mono ss-v ' + ((() => { const up = D.stocks.filter(s=>s.ch>0).length; return up > D.stocks.length/2 ? 't-up' : 't-dim'; })())}>
          {D.stocks.filter(s=>s.ch>0).length}
        </span>
        <span className="ss-u">up</span>
        <span className="mono t-down ss-v">{D.stocks.filter(s=>s.ch<0).length}</span>
        <span className="ss-u">dn</span>
        <div className="grow"/>
        <span className="ss-k">TASE</span>
        <span className="ss-v">{_isTaseOpen() ? <span><span className="dot dot-green"/>OPEN</span> : <span><span className="dot dot-red"/>CLOSED</span>}</span>
      </div>
    </div>
  );
};

// TASE hours: Sun–Thu 09:30–17:30 Israel Time (UTC+3)
function _isTaseOpen() {
  const now = new Date();
  const utcH = now.getUTCHours(), utcM = now.getUTCMinutes();
  const ilMin = utcH * 60 + utcM + 180; // Israel = UTC+3
  const dow = (now.getUTCDay() + Math.floor(ilMin / 1440)) % 7; // adjust for midnight crossover
  const todayMin = ilMin % 1440;
  const isWeekday = dow >= 0 && dow <= 4; // Sun=0…Thu=4
  return isWeekday && todayMin >= 570 && todayMin <= 1050; // 9:30–17:30
}

window.FilterRail = function FilterRail() {
  const presets = ['All','Large Cap','Value','Growth','Dividend','Momentum','Recent IPOs','Quality','Profitable','Compounders','FCF Value','Liquid','נדל"ן','Dual Listed ⇄'];
  const [active, setActive] = useStateChrome('All');
  const fields = [
    ['SEARCH','Ticker, name, sector…',180,true],
    ['P/E <','e.g. 25',60],['P/S <','e.g. 3',60],['MCAP > (M)','500',70],
    ['DIV > %','3',50],['CHG > %','-5',55],['ROE > %','10',55],['REV > %','10',55],['D/E <','1',50],
    ['ROIC > %','15',55],['EV/FCF <','15',60],['QUICK >','1',55],['ADV > ₪M','500',60]
  ];
  return (
    <div className="filter-rail">
      <div className="presets">
        {presets.map(p => (
          <button key={p} className={'preset ' + (p===active && 'is-active')} onClick={()=>setActive(p)}>{p}</button>
        ))}
      </div>
      <div className="inputs">
        {fields.map(([l,ph,w,wide]) => (
          <label key={l} className={'inp ' + (wide && 'inp-wide')}>
            <span className="inp-l">{l}</span>
            <input className="inp-i mono" placeholder={ph} style={w?{width:w}:null}/>
          </label>
        ))}
        <button className="btn btn-ghost btn-sm">× Clear</button>
      </div>
    </div>
  );
};
