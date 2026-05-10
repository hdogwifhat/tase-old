// Main app — wires routing, ccy, tweaks, and live data loading
const { useState: useAppState } = React;

const TWEAK_DEFAULTS = /*EDITMODE-BEGIN*/{
  "accent": "#3b82f6",
  "accent2": "#f59e0b",
  "density": "compact",
  "fontMono": "JetBrains Mono",
  "showBidirIsolation": true,
  "tickerColor": "accent",
  "rowZebra": true,
  "stickyHeader": true,
  "showSparklines": true
}/*EDITMODE-END*/;

function App() {
  const [t, setTweak] = useTweaks(TWEAK_DEFAULTS);
  const [route, setRoute] = useAppState('home');
  const [ccy, setCcy] = useAppState('ILS');
  const [selectedTicker, setSelectedTicker] = useAppState('MZTF');
  // Increment whenever data.jsx signals new data is ready
  const [dataVersion, setDataVersion] = useAppState(0);

  // Expose refresh callback for data.jsx
  window._refreshReact = () => setDataVersion(v => v + 1);

  // Apply tweaks via CSS vars
  useEffect(() => {
    const r = document.documentElement;
    r.style.setProperty('--accent', t.accent);
    r.style.setProperty('--accent-2', t.accent2);
    r.style.setProperty('--font-mono', `'${t.fontMono}', ui-monospace, SF Mono, Menlo, monospace`);
    r.dataset.density = t.density;
    r.dataset.zebra = t.rowZebra ? '1' : '0';
    r.dataset.sticky = t.stickyHeader ? '1' : '0';
    r.dataset.sparks = t.showSparklines ? '1' : '0';
    r.dataset.tickercolor = t.tickerColor;
  }, [t]);

  const D = window.TASE_DATA;

  // ── Loading / error states ───────────────────────────────────────────────
  if (D._loading && D.stocks.length === 0) {
    return (
      <div className="app" style={{display:'flex',alignItems:'center',justifyContent:'center',minHeight:'100vh'}}>
        <div style={{textAlign:'center',color:'var(--t2)'}}>
          <div style={{fontSize:28,marginBottom:12,color:'var(--accent)'}}>TA</div>
          <div style={{fontFamily:'var(--font-mono)',fontSize:13,marginBottom:8}}>
            {D._error ? D._error : 'Fetching TASE data… 60–90 s on first load'}
          </div>
          <div style={{fontSize:11,color:'var(--t3)'}}>
            {D._error
              ? <span>Will retry automatically · <a href="#" style={{color:'var(--accent)'}} onClick={e=>{e.preventDefault();window._fetchAndPopulate&&window._fetchAndPopulate();}}>retry now</a></span>
              : 'Pulling all ~550 stocks from Tel Aviv Stock Exchange'}
          </div>
          <div className="loading-dots" style={{marginTop:20}}>
            <span style={{animation:'blink 1.2s infinite 0s'}}>▪</span>
            <span style={{animation:'blink 1.2s infinite 0.4s'}}>▪</span>
            <span style={{animation:'blink 1.2s infinite 0.8s'}}>▪</span>
          </div>
        </div>
        <style>{`
          @keyframes blink { 0%,80%,100%{opacity:0.15} 40%{opacity:1} }
          .loading-dots span { color:var(--accent); font-size:20px; margin:0 4px; display:inline-block; }
        `}</style>
      </div>
    );
  }

  const showRibbon = !['detail','dcf'].includes(route);
  const showFilters = ['home','list'].includes(route);

  return (
    <div className="app" data-screen-label={`TASE — ${route}`}>
      {showRibbon && (
        <TopChrome route={route} setRoute={setRoute} ccy={ccy} setCcy={setCcy} density={t.density} setDensity={(v)=>setTweak('density',v)}/>
      )}
      {showFilters && <FilterRail/>}

      <main className={'app-main route-' + route}>
        {route === 'home' && <ScreenHome ccy={ccy} setRoute={setRoute} setSelectedTicker={setSelectedTicker}/>}
        {route === 'list' && <ScreenList ccy={ccy} setRoute={setRoute} setSelectedTicker={setSelectedTicker}/>}
        {route === 'heat' && <ScreenHeat/>}
        {route === 'detail' && <ScreenDetail ticker={selectedTicker} ccy={ccy} setRoute={setRoute}/>}
        {route === 'dcf' && <ScreenDCF ccy={ccy} setRoute={setRoute}/>}
        {route === 'bonds' && <ScreenBonds/>}
        {['short','sect','val','qual','breadth','fav'].includes(route) && (
          <div className="screen screen-stub" data-screen-label={'STUB — '+route}>
            <div className="stub-card">
              <div className="stub-t">{route.toUpperCase()}</div>
              <div className="stub-s">This view is wired into the routing but not built out yet. The same density rules, sticky headers, and monospace numerics apply.</div>
              <button className="btn btn-primary" onClick={()=>setRoute('list')}>← Back to Stock List</button>
            </div>
          </div>
        )}
      </main>

      {/* Live data banner when stocks are stale/fetching in background */}
      {D._fetching && D.stocks.length > 0 && (
        <div style={{position:'fixed',bottom:8,left:'50%',transform:'translateX(-50%)',
          background:'var(--s2)',border:'1px solid var(--bd)',borderRadius:6,
          padding:'4px 12px',fontSize:11,color:'var(--t2)',zIndex:999}}>
          ↻ Refreshing market data…
        </div>
      )}

      <TweaksPanel title="Tweaks">
        <TweakSection label="Aesthetic"/>
        <TweakColor label="Accent" value={t.accent} options={['#3b82f6','#22d3ee','#a78bfa','#f59e0b','#10b981']} onChange={v=>setTweak('accent', v)}/>
        <TweakColor label="Accent 2" value={t.accent2} options={['#f59e0b','#3b82f6','#a78bfa','#ef4444','#10b981']} onChange={v=>setTweak('accent2', v)}/>
        <TweakRadio label="Ticker color" value={t.tickerColor} options={['accent','white']} onChange={v=>setTweak('tickerColor', v)}/>

        <TweakSection label="Density"/>
        <TweakRadio label="Row density" value={t.density} options={['compact','regular','comfy']} onChange={v=>setTweak('density', v)}/>
        <TweakSelect label="Mono font" value={t.fontMono} options={['JetBrains Mono','IBM Plex Mono','Roboto Mono','ui-monospace']} onChange={v=>setTweak('fontMono', v)}/>

        <TweakSection label="Tables"/>
        <TweakToggle label="Zebra striping" value={t.rowZebra} onChange={v=>setTweak('rowZebra', v)}/>
        <TweakToggle label="Sticky header" value={t.stickyHeader} onChange={v=>setTweak('stickyHeader', v)}/>
        <TweakToggle label="Sparklines in list" value={t.showSparklines} onChange={v=>setTweak('showSparklines', v)}/>
      </TweaksPanel>
    </div>
  );
}

// Expose _fetchAndPopulate for manual retry button
window._fetchAndPopulate = window._fetchAndPopulate || (() => {});

ReactDOM.createRoot(document.getElementById('root')).render(<App/>);
