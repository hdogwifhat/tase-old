// DCF Calculator
window.ScreenDCF = function ScreenDCF({ccy, setRoute}) {
  const D = window.TASE_DATA;
  const [ticker, setTicker] = useState('MZTF');
  const stock = D.stocks.find(x => x.t === ticker) || D.stocks[0] || {t:'—',n:'—',p:100,mc:0,pe:null,ev:null,roe:null,nm:null};

  // Tunable inputs
  const [g1, setG1] = useState(0.5);
  const [g2, setG2] = useState(8.0);
  const [tg, setTg] = useState(2.5);
  const [ebit0, setEbit0] = useState(62.7);
  const [ebit10, setEbit10] = useState(50.0);
  const [da, setDa] = useState(4);
  const [capex, setCapex] = useState(5);
  const [dnwc, setDnwc] = useState(3);
  const [tax, setTax] = useState(23);
  const [rfr, setRfr] = useState(4.38);
  const [beta, setBeta] = useState(0.20);
  const [erp, setErp] = useState(5.5);
  const [kd, setKd] = useState(5.5);
  const [debtW, setDebtW] = useState(30);
  const [rev0, setRev0] = useState(1000);
  const [shares, setShares] = useState(100);
  const [netDebt, setNetDebt] = useState(0);

  // Computed
  const ke = rfr + beta * erp;
  const wacc = (1 - debtW/100) * ke + (debtW/100) * kd * (1 - tax/100);
  const intrinsic = useMemo(() => calcDCF({rev0, g1, g2, tg, ebit0, ebit10, da, capex, dnwc, tax, wacc, shares, netDebt}), [rev0,g1,g2,tg,ebit0,ebit10,da,capex,dnwc,tax,wacc,shares,netDebt]);

  const market = stock.p;
  const vsMkt = ((intrinsic.perShare - market) / market) * 100;

  return (
    <div className="screen screen-dcf" data-screen-label="06 DCF">
      <aside className="dcf-side">
        <button className="d-back" onClick={()=>setRoute('list')}>← Screener</button>

        <div className="dcf-head">
          <div className="dcf-h-t">DCF Valuation Calculator</div>
          <div className="dcf-h-s">Discounted Cash Flow · Hedge-Fund Grade · TASE</div>
        </div>

        <div className="dcf-sect">
          <div className="dcf-l">SELECT STOCK</div>
          <select className="dcf-stock-sel" value={ticker} onChange={e=>setTicker(e.target.value)}>
            {D.stocks.map(s => <option key={s.t} value={s.t}>{s.t} — {s.n}</option>)}
          </select>
        </div>

        <div className="dcf-quickstats">
          <QS l="PRICE" v={'₪'+fmt.num(stock.p,2)}/>
          <QS l="MKT CAP" v={fmt.mc(stock.mc)}/>
          <QS l="P/E TTM" v={stock.pe?.toFixed(1)+'x'}/>
          <QS l="EV/EBITDA" v={stock.ev != null ? stock.ev.toFixed(1)+'x' : '—'}/>
          <QS l="ROE" v={(stock.roe?.toFixed(1) ?? '—')+'%'} delta={stock.roe}/>
          <QS l="NET MARGIN" v={(stock.nm?.toFixed(1) ?? '—')+'%'} delta={stock.nm}/>
        </div>

        <details open className="dcf-acc">
          <summary><span className="dcf-acc-i">📈</span> REVENUE GROWTH</summary>
          <DCFSlider l="Phase 1: Years 1-5" v={g1} set={setG1} min={-5} max={20} step={0.1} unit="%"/>
          <DCFSlider l="Phase 2: Years 6-10" v={g2} set={setG2} min={-5} max={25} step={0.1} unit="%"/>
          <DCFSlider l="Terminal Growth Rate" v={tg} set={setTg} min={0} max={5} step={0.1} unit="%"/>
        </details>

        <details open className="dcf-acc">
          <summary><span className="dcf-acc-i">📊</span> MARGIN ASSUMPTIONS</summary>
          <DCFInput l="Current EBIT Margin" sub="Base year (auto-filled from TTM)" v={ebit0} set={setEbit0} unit="%"/>
          <DCFInput l="Target EBIT Margin (Y10)" sub="Linear interpolation over 10 years" v={ebit10} set={setEbit10} unit="%"/>
          <DCFInput l="D&A as % of Revenue" v={da} set={setDa} unit="%"/>
          <DCFInput l="CapEx as % of Revenue" v={capex} set={setCapex} unit="%"/>
          <DCFInput l="ΔWorking Capital as % of ΔRev" v={dnwc} set={setDnwc} unit="%"/>
          <DCFInput l="Tax Rate" sub="Israeli corporate tax = 23%" v={tax} set={setTax} unit="%"/>
        </details>

        <details open className="dcf-acc">
          <summary><span className="dcf-acc-i">💸</span> DISCOUNT RATE (WACC)</summary>
          <div className="dcf-mode">
            <button className="dcf-mode-btn is-active">Auto (CAPM)</button>
            <button className="dcf-mode-btn">Manual</button>
          </div>
          <DCFInput l="Risk-Free Rate" sub="IL 10Y gov bond ≈ 4.38%" v={rfr} set={setRfr} unit="%"/>
          <DCFInput l="Beta" sub="Auto-filled from screener" v={beta} set={setBeta} unit=""/>
          <DCFInput l="Equity Risk Premium" sub="Damodaran Israel 2024 ≈ 5.5%" v={erp} set={setErp} unit="%"/>
          <DCFInput l="Pre-Tax Cost of Debt" v={kd} set={setKd} unit="%"/>
          <DCFInput l="Debt Weight (D/V)" v={debtW} set={setDebtW} unit="%"/>
          <div className="dcf-computed">
            <span>Computed WACC</span>
            <span className="mono dcf-wacc">{wacc.toFixed(2)}%</span>
          </div>
        </details>

        <details className="dcf-acc">
          <summary><span className="dcf-acc-i">📋</span> BASE FINANCIAL DATA</summary>
          <DCFInput l="Last Year Revenue" sub="ILS millions (auto-filled)" v={rev0} set={setRev0} unit=""/>
          <DCFInput l="Shares Outstanding" sub="millions" v={shares} set={setShares} unit=""/>
          <DCFInput l="Net Debt (Debt − Cash)" sub="ILS millions. Negative = net cash" v={netDebt} set={setNetDebt} unit=""/>
          <DCFInput l="Current Share Price" sub="(auto-filled)" v={stock.p} set={()=>{}} unit=""/>
        </details>

        <details className="dcf-acc">
          <summary><span className="dcf-acc-i">⚙</span> MODEL TOGGLES</summary>
          <DCFCheck l="Include Terminal Value" sub="Gordon Growth model, perpetuity beyond Y10"/>
          <DCFCheck l="Add Cash to Equity Value" sub="Cash &amp; short-term investments from balance sheet"/>
          <DCFCheck l="Subtract Net Debt" sub="Standard equity value bridge"/>
        </details>
      </aside>

      <div className="dcf-main">
        <div className="dcf-topbar">
          <button className="btn btn-primary btn-lg">▶ Run DCF</button>
        </div>

        <div className="dcf-headline">
          <div className="dcf-iv">
            <div className="dcf-iv-l">INTRINSIC VALUE PER SHARE</div>
            <div className="dcf-iv-v mono">
              <span className="iv-sym">₪</span>{fmt.num(intrinsic.perShare, 2)}
            </div>
            <div className="dcf-iv-vs mono">
              <span className={vsMkt >= 0 ? 't-up' : 't-down'}>{vsMkt >= 0 ? '+' : ''}{vsMkt.toFixed(1)}%</span>
              <span className="t-dim"> vs ₪{fmt.num(market,2)} market</span>
            </div>
          </div>
          <div className="dcf-bridge-tiles">
            <BridgeTile l="ENT. VALUE" v={'₪'+fmt.mcShort(intrinsic.ev*1000)}/>
            <BridgeTile l="EQUITY VALUE" v={'₪'+fmt.mcShort(intrinsic.equity*1000)}/>
            <BridgeTile l="PV OF FCFS" v={'₪'+fmt.mcShort(intrinsic.pvFcfs*1000)}/>
            <BridgeTile l="PV TERM. VAL." v={'₪'+fmt.mcShort(intrinsic.pvTv*1000)}/>
            <BridgeTile l="TV % OF EV" v={(intrinsic.pvTv / intrinsic.ev * 100).toFixed(1) + '%'}/>
            <BridgeTile l="SHARES OUT." v={shares.toFixed(1) + 'M'}/>
          </div>
        </div>

        <div className="dcf-chips">
          <Chip l="WACC" v={wacc.toFixed(2)+'%'} tone="info"/>
          <Chip l="Ter. Growth" v={tg.toFixed(1)+'%'}/>
          <Chip l="G1 (Y1-5)" v={g1.toFixed(1)+'%'}/>
          <Chip l="G2 (Y6-10)" v={g2.toFixed(1)+'%'}/>
          <Chip l="EBIT₀" v={ebit0.toFixed(1)+'%'}/>
          <Chip l="EBIT₁₀" v={ebit10.toFixed(1)+'%'}/>
        </div>

        <div className="dcf-bridge">
          <div className="dcf-bridge-h">VALUE BRIDGE</div>
          <BridgeRow l="PV of FCFs (Y1-10)" v={intrinsic.pvFcfs} max={intrinsic.ev*1.1} color="var(--accent)"/>
          <BridgeRow l="PV of Terminal Value" v={intrinsic.pvTv} max={intrinsic.ev*1.1} color="var(--accent-2)"/>
          <BridgeRow l="Enterprise Value" v={intrinsic.ev} max={intrinsic.ev*1.1} color="var(--t1)"/>
          <BridgeRow l="Minus Net Debt" v={netDebt/1000} max={intrinsic.ev*1.1} color="var(--down)"/>
          <BridgeRow l="Equity Value" v={intrinsic.equity} max={intrinsic.ev*1.1} color="var(--up)"/>
        </div>

        <div className="dcf-projection">
          <div className="dcf-bridge-h">10-YEAR FREE CASH FLOW PROJECTIONS</div>
          <div className="dcf-proj-tbl mono-tbl" style={{['--cols']:'48px repeat(11, 1fr)'}}>
            <div className="tr tr-h">
              {['YEAR','REVENUE','REV GR','EBIT %','EBIT','NOPAT','D&A','CAPEX','ΔNWC','FCF','DISC F','PV FCF'].map(h => (
                <div key={h} className={'th ' + (h==='YEAR' ? 'th-l' : 'c-num')}>{h}</div>
              ))}
            </div>
            {intrinsic.years.map((y, i) => (
              <div key={y.y} className={'tr ' + (i%2 ? 'tr-z' : '')}>
                <div className="td td-l mono">Y{y.y}</div>
                <div className="td c-num mono">₪{y.rev.toFixed(2)}B</div>
                <div className="td c-num mono"><Delta v={y.gr*100} d={1}/></div>
                <div className="td c-num mono">{(y.ebitMargin*100).toFixed(1)}%</div>
                <div className="td c-num mono">₪{(y.ebit*1000).toFixed(1)}M</div>
                <div className="td c-num mono">₪{(y.nopat*1000).toFixed(1)}M</div>
                <div className="td c-num mono">₪{(y.da*1000).toFixed(1)}M</div>
                <div className="td c-num mono t-down">(₪{Math.abs(y.capex*1000).toFixed(1)}M)</div>
                <div className="td c-num mono t-down">(₪{Math.abs(y.dnwc*1000).toFixed(1)}M)</div>
                <div className="td c-num mono">₪{(y.fcf*1000).toFixed(1)}M</div>
                <div className="td c-num mono t-dim">{y.disc.toFixed(4)}</div>
                <div className="td c-num mono t-fg-strong">₪{(y.pvfcf*1000).toFixed(1)}M</div>
              </div>
            ))}
            <div className="tr tr-term">
              <div className="td td-l mono t-warn">Terminal</div>
              {[...Array(8)].map((_,i)=><div key={i} className="td c-num mono t-dim">—</div>)}
              <div className="td c-num mono t-warn">₪{intrinsic.tv.toFixed(2)}B</div>
              <div className="td c-num mono t-dim">{intrinsic.discTerm.toFixed(4)}</div>
              <div className="td c-num mono t-warn">₪{intrinsic.pvTv.toFixed(2)}B</div>
            </div>
            <div className="tr tr-tot">
              <div className="td td-l mono">Total</div>
              {[...Array(10)].map((_,i)=><div key={i} className="td c-num mono t-dim">—</div>)}
              <div className="td c-num mono t-fg-strong">₪{intrinsic.ev.toFixed(2)}B</div>
            </div>
          </div>
        </div>

        <div className="sens">
          <div className="sens-h">
            <span>SENSITIVITY: INTRINSIC VALUE (₪) — WACC × TERMINAL GROWTH</span>
            <span className="sens-leg">
              <span className="t-dim">Highlighted = current.</span>
              <span className="leg-sw" style={{background:'oklch(40% 0.13 145)'}}/> upside
              <span className="leg-sw" style={{background:'oklch(35% 0.14 25)'}}/> downside
              <span className="t-dim">vs ₪{fmt.num(market,2)}</span>
            </span>
          </div>
          <SensitivityGrid wacc={wacc} tg={tg} market={market} base={{rev0, g1, g2, ebit0, ebit10, da, capex, dnwc, tax, shares, netDebt}}/>
        </div>
      </div>
    </div>
  );
};

function calcDCF({rev0, g1, g2, tg, ebit0, ebit10, da, capex, dnwc, tax, wacc, shares, netDebt}) {
  const years = [];
  let rev = rev0/1000; // to billions
  const w = wacc/100, gT = tg/100;
  for (let y = 1; y <= 10; y++) {
    const gr = y <= 5 ? g1/100 : g2/100;
    rev *= (1 + gr);
    const em = (ebit0 + (ebit10 - ebit0) * (y/10)) / 100;
    const ebit = rev * em;
    const nopat = ebit * (1 - tax/100);
    const daV = rev * (da/100);
    const capexV = -rev * (capex/100);
    const dnwcV = -(rev * gr) * (dnwc/100);
    const fcf = nopat + daV + capexV + dnwcV;
    const disc = 1 / Math.pow(1+w, y);
    years.push({y, rev, gr, ebitMargin: em, ebit, nopat, da: daV, capex: capexV, dnwc: dnwcV, fcf, disc, pvfcf: fcf*disc});
  }
  const lastFcf = years[9].fcf;
  const tv = (lastFcf * (1 + gT)) / Math.max(0.001, w - gT);
  const discTerm = 1 / Math.pow(1+w, 10);
  const pvTv = tv * discTerm;
  const pvFcfs = years.reduce((a,b) => a + b.pvfcf, 0);
  const ev = pvFcfs + pvTv;
  const equity = ev - netDebt/1000;
  const perShare = equity * 1000 / shares;
  return {years, tv, discTerm, pvTv, pvFcfs, ev, equity, perShare};
}

function SensitivityGrid({wacc, tg, market, base}) {
  const tgs = [0, 0.5, 1, 1.5, 2, 2.5, 3, 3.5, 4];
  const waccs = [3.11, 3.61, 4.11, 4.61, 5.11, 5.61, 6.11, 6.61, 7.11];
  return (
    <div className="sens-grid" style={{gridTemplateColumns: `64px repeat(${tgs.length}, 1fr)`}}>
      <div className="sens-cell sens-corner">WACC \ TG</div>
      {tgs.map(t => <div key={t} className="sens-cell sens-h-cell mono">{t.toFixed(1)}%</div>)}
      {waccs.map(w => (
        <React.Fragment key={w}>
          <div className="sens-cell sens-h-cell mono">{w.toFixed(2)}%</div>
          {tgs.map(t => {
            if (w - t <= 0.5) return <div key={t} className="sens-cell sens-na mono">N/A</div>;
            const r = calcDCF({...base, tg: t, wacc: w});
            const v = r.perShare;
            const vs = ((v - market)/market)*100;
            const isCurr = Math.abs(w - wacc) < 0.3 && Math.abs(t - tg) < 0.3;
            const cls = ['sens-cell mono', vs >= 0 ? 'sens-up' : 'sens-dn', isCurr && 'is-current'].filter(Boolean).join(' ');
            const intensity = Math.min(1, Math.abs(vs)/80);
            const bg = vs >= 0
              ? `oklch(${22 + intensity*22}% 0.13 145)`
              : `oklch(${22 + intensity*15}% 0.14 25)`;
            return (
              <div key={t} className={cls} style={{background: bg}}>
                <div className="sens-v">₪{v >= 1000 ? (v/1000).toFixed(1)+'k' : v.toFixed(1)}</div>
                <div className="sens-vs">{vs >= 0 ? '+' : ''}{vs.toFixed(0)}%</div>
              </div>
            );
          })}
        </React.Fragment>
      ))}
    </div>
  );
}

function QS({l, v, delta}) {
  return (
    <div className="qs">
      <div className="qs-l">{l}</div>
      <div className={'qs-v mono ' + (delta != null ? (delta >= 0 ? 't-up' : 't-down') : '')}>{v}</div>
    </div>
  );
}

function BridgeTile({l, v}) {
  return <div className="brt"><div className="brt-l">{l}</div><div className="brt-v mono">{v}</div></div>;
}

function Chip({l, v, tone}) {
  return <div className={'chip ' + (tone ? 'chip-'+tone : '')}><span className="chip-l">{l}</span> <span className="mono chip-v">{v}</span></div>;
}

function BridgeRow({l, v, max, color}) {
  const w = Math.max(2, (Math.abs(v) / max) * 100);
  return (
    <div className="br-row">
      <div className="br-l">{l}</div>
      <div className="br-bar"><div className="br-fill" style={{width: w + '%', background: color}}/></div>
      <div className="br-v mono">₪{Math.abs(v).toFixed(2)}B</div>
    </div>
  );
}

function DCFSlider({l, v, set, min, max, step, unit}) {
  return (
    <div className="dcf-ctrl">
      <div className="dcf-ctrl-h">
        <span className="dcf-ctrl-l">{l}</span>
        <span className="mono dcf-ctrl-v t-up">{v.toFixed(1)}{unit}</span>
      </div>
      <div className="dcf-ctrl-row">
        <input type="range" min={min} max={max} step={step} value={v} onChange={e=>set(parseFloat(e.target.value))} className="dcf-range"/>
        <input type="number" step={step} value={v} onChange={e=>set(parseFloat(e.target.value)||0)} className="dcf-num mono"/>
      </div>
    </div>
  );
}

function DCFInput({l, sub, v, set, unit}) {
  return (
    <div className="dcf-ctrl dcf-ctrl-tight">
      <div className="dcf-ctrl-l">
        <div>{l}</div>
        {sub && <div className="dcf-ctrl-sub">{sub}</div>}
      </div>
      <div className="dcf-num-wrap">
        <input type="number" step="0.1" value={v} onChange={e=>set(parseFloat(e.target.value)||0)} className="dcf-num mono"/>
        {unit && <span className="dcf-num-u mono">{unit}</span>}
      </div>
    </div>
  );
}

function DCFCheck({l, sub}) {
  const [v, setV] = useState(true);
  return (
    <label className="dcf-check">
      <input type="checkbox" checked={v} onChange={e=>setV(e.target.checked)}/>
      <div>
        <div>{l}</div>
        {sub && <div className="dcf-ctrl-sub">{sub}</div>}
      </div>
    </label>
  );
}

// Bonds screen
window.ScreenBonds = function ScreenBonds() {
  const D = window.TASE_DATA;
  return (
    <div className="screen screen-bonds" data-screen-label="07 Bonds">
      <div className="bonds-h">
        <h2 className="bonds-t"><Hebrew>אג"ח</Hebrew> Bonds</h2>
        <span className="t-dim">Israeli corporate bonds · all series</span>
        <div className="grow"/>
        <div className="seg seg-sm">
          <button className="seg-btn is-active">All</button>
          <button className="seg-btn">Linked CPI</button>
          <button className="seg-btn">Shekel Linked</button>
          <button className="seg-btn">Government</button>
        </div>
      </div>
      <div className="tbl-wrap">
        <div className="tbl mono-tbl bonds-tbl" style={{['--cols']:'72px 1.4fr 50px 80px 70px 70px 80px 90px 130px 70px 70px 70px 70px 100px 110px 80px'}}>
          <div className="tr tr-h">
            {['TICKER','ISSUER','SER','PRICE','CHG %','YTM','VOLUME','ADV','RANGE','MOD','DUR','CPN','YTW','RATING','SECTOR','MAYA'].map(h => (
              <div key={h} className={'th ' + (['PRICE','CHG %','YTM','VOLUME','ADV','MOD','DUR','CPN','YTW'].includes(h) ? 'c-num' : '')}>{h}</div>
            ))}
          </div>
          {D.bonds.map((b, i) => (
            <div key={b.t+b.ser+i} className={'tr ' + (i%2 ? 'tr-z' : '')}>
              <div className="td c-tick mono">{b.t}</div>
              <div className="td c-name">{b.n}</div>
              <div className="td mono t-dim">{b.ser}</div>
              <div className="td c-num mono">{b.p.toFixed(2)}</div>
              <div className="td c-num mono"><Delta v={b.ch} d={2}/></div>
              <div className="td c-num mono"><Delta v={b.ytm} d={1}/></div>
              <div className="td c-num mono t-dim">{fmt.vol(b.vol)}</div>
              <div className="td c-num mono">₪{b.ad?.toFixed(2) ?? '0'}</div>
              <div className="td c-num mono t-dim">{b.range}</div>
              <div className="td c-num mono">{b.mod?.toFixed(2) ?? <span className="t-dim">—</span>}</div>
              <div className="td c-num mono">{b.dur != null ? <Delta v={b.dur} d={1}/> : <span className="t-dim">—</span>}</div>
              <div className="td c-num mono">{b.cpn != null ? <Delta v={b.cpn} d={1}/> : <span className="t-dim">—</span>}</div>
              <div className="td c-num mono">{b.ytw != null ? <Delta v={b.ytw} d={1}/> : <span className="t-dim">—</span>}</div>
              <div className="td">{b.rt ? <Pill tone="rating">{b.rt}</Pill> : <span className="t-dim">—</span>}</div>
              <div className="td t-dim">{b.sec2 || '—'}</div>
              <div className="td"><a className="link mono">Maya ↗</a></div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
};
