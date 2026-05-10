// Stock Detail screen — sidebar nav, header, KPI strip, range, stat blocks, chart, financials, peers
window.ScreenDetail = function ScreenDetail({ticker, ccy, setRoute}) {
  const D = window.TASE_DATA;
  const ilsPerUsd = D._fx?.ils_per_usd || 3.65;
  const stock = D.stocks.find(x => x.t === ticker) || D.stocks[0]; // fallback to first stock
  const [tab, setTab] = useState('overview');

  // Range pct of 52w — guard against missing data
  const rangePct = (stock.w52h && stock.w52l && stock.w52h > stock.w52l)
    ? Math.max(0, Math.min(100, ((stock.p - stock.w52l) / (stock.w52h - stock.w52l) * 100))).toFixed(0)
    : '50';

  return (
    <div className="screen screen-detail" data-screen-label="04 Stock Detail">
      <aside className="d-side">
        <button className="d-back" onClick={()=>setRoute('list')}>← Screener</button>
        <div className="d-side-h">NAVIGATION</div>
        {[
          ['overview','◆ Overview'],['stats','≡ Statistics'],['fin','∫ Financials'],
          ['news','◇ News'],['peers','▦ Peers']
        ].map(([k,l]) => (
          <button key={k} className={'d-nav ' + (tab===k && 'is-active')} onClick={()=>setTab(k)}>{l}</button>
        ))}
        <div className="d-clock">
          <div className="d-time mono">22:19:51</div>
          <div className="d-stat"><span className="dot dot-red"/>TASE Closed</div>
        </div>
      </aside>

      <div className="d-main">
        <div className="d-bar">
          <div className="d-tick mono">{stock.t}</div>
          <div className="d-name">{stock.n}</div>
          <div className="d-he"><Hebrew>{stock.he}</Hebrew></div>
          <div className="grow"/>
          <div className="d-price-mini mono">{ccy==='ILS'?'₪':'$'}{fmt.num(ccy==='ILS'?stock.p:stock.p/ilsPerUsd,2)}</div>
          <div className="d-chg-mini"><Delta v={stock.ch} d={2}/></div>
        </div>

        {tab === 'overview' && <OverviewTab stock={stock} ccy={ccy} rangePct={rangePct}/>}
        {tab === 'stats' && <StatsTab stock={stock} ccy={ccy}/>}
        {tab === 'fin' && <FinancialsTab stock={stock}/>}
        {tab === 'news' && <NewsTab stock={stock}/>}
        {tab === 'peers' && <PeersTab stock={stock}/>}
      </div>
    </div>
  );
};

function OverviewTab({stock, ccy, rangePct}) {
  return (
    <div className="d-tab">
      <div className="d-hdr">
        <div className="d-hdr-l">
          <div className="d-tick-lg mono">{stock.t}</div>
          <div className="d-name-lg">{stock.n}</div>
        </div>
        <div className="d-pills">
          <Pill tone="info">TASE · TLV</Pill>
          <Pill tone="warn">{stock.s}</Pill>
          <Pill>{stock.s.split(' ')[0]} · Regional</Pill>
        </div>
        <div className="d-desc">
          {stock.n}, together with its subsidiaries, provides a range of international, commercial, domestic, and personal banking services to individuals and businesses in Israel and internationally. It operates through six segments including Small Business, Private Banking, Commercial Banking and Financial Management.
        </div>
        <div className="d-meta">
          <span>CEO <span className="t-fg">Moshe Lari</span></span>
          <span className="t-dim">·</span>
          <span>Employees <span className="mono t-fg">7,211</span></span>
          <span className="t-dim">·</span>
          <span>IPO <span className="mono t-fg">2000-02-01</span></span>
          <span className="t-dim">·</span>
          <a className="link" href="#">www.{stock.t.toLowerCase()}.co.il</a>
        </div>

        <div className="d-price-row">
          <div className="d-price">
            <span className="dp-sym">{ccy==='ILS'?'₪':'$'}</span>
            <span className="dp-v mono">{fmt.num(ccy==='ILS'?stock.p:stock.p/ilsPerUsd,2)}</span>
          </div>
          <div className="d-price-meta">
            <div className="dpm-l"><Delta v={stock.ch} d={2} weight={600}/> <span className="t-dim mono">(+{(stock.p*stock.ch/100).toFixed(2)})</span></div>
            <div className="dpm-s">
              Mkt Cap <span className="mono t-fg">{fmt.mc(stock.mc)}</span> ·
              EPS <span className="mono t-fg">₪{stock.eps?.toFixed(2)}</span> ·
              Div Yld <span className="mono t-fg">{stock.div != null ? stock.div.toFixed(2)+'%' : '—'}</span>
            </div>
          </div>
        </div>
      </div>

      <div className="kpi-strip">
        {[
          ['OPEN', stock.p*0.998, 'mn'],
          ['HIGH', stock.p*1.012, 'mn'],
          ['LOW', stock.p*0.988, 'mn'],
          ['PREV CLOSE', stock.p/(1+stock.ch/100), 'mn'],
          ['VOLUME', stock.vol, 'vol'],
          ['ADV (3M)', stock.mc*0.001, 'mc'],
          ['52W HIGH', stock.w52h, 'mn'],
          ['52W LOW', stock.w52l, 'mn'],
        ].map(([l,v,kind]) => (
          <div key={l} className="ks">
            <div className="ks-l">{l}</div>
            <div className="ks-v mono">
              {kind==='mn' && <>₪{fmt.num(v,2)}</>}
              {kind==='vol' && fmt.vol(v)}
              {kind==='mc' && <>₪{fmt.mcShort(v)}</>}
            </div>
          </div>
        ))}
      </div>

      <div className="range-block">
        <div className="range-h"><span>52-WEEK RANGE</span></div>
        <div className="range-bar">
          <div className="range-fill" style={{width: rangePct + '%'}}/>
          <div className="range-marker" style={{left: rangePct + '%'}}>
            <div className="range-marker-pct mono">{rangePct}% of range</div>
          </div>
        </div>
        <div className="range-labels">
          <span className="mono">₪{fmt.num(stock.w52l,1)}</span>
          <span className="mono">₪{fmt.num(stock.w52h,1)}</span>
        </div>
      </div>

      <div className="stat-grid">
        <StatBlock title="VALUATION" rows={[
          ['P/E', stock.pe?.toFixed(1)+'x'],
          ['Fwd P/E', stock.fpe?.toFixed(1)+'x'],
          ['EV/EBITDA', stock.ev != null ? stock.ev.toFixed(1)+'x' : '—'],
          ['P/S', stock.ps?.toFixed(1)+'x'],
          ['P/B', stock.pb?.toFixed(1)+'x'],
          ['EV/FCF', stock.evfcf != null ? stock.evfcf.toFixed(1)+'x' : '—'],
          ['Mkt Cap', fmt.mc(stock.mc)],
        ]}/>
        <StatBlock title="PROFITABILITY" rows={[
          ['ROE', <Delta v={stock.roe} d={1}/>],
          ['ROIC', '—'],
          ['ROA', <Delta v={stock.roe*0.1} d={1}/>],
          ['Gross Mgn', stock.gm + '%'],
          ['Op Mgn', <Delta v={stock.opm} d={1}/>],
          ['Net Mgn', <Delta v={stock.nm} d={1}/>],
          ['Rev Δ', <Delta v={stock.rev} d={1}/>],
          ['EPS Δ', <Delta v={stock.rev*0.5} d={1}/>],
        ]}/>
        <StatBlock title="FINANCIAL HEALTH" rows={[
          ['D/E', stock.dscr?.toFixed(2) ?? '—'],
          ['Current', '—'],
          ['Quick', stock.qr?.toFixed(2) ?? '—'],
          ['WACC', '7.8%'],
          ['Beta', stock.beta?.toFixed(2)],
          ['Div Yld', stock.div != null ? stock.div.toFixed(2)+'%' : '—'],
          ['EPS (TTM)', '₪'+stock.eps?.toFixed(2)],
          ['IPO', '2000-02-01'],
        ]}/>
        <StatBlock title="MARKET / 52W" rows={[
          ['52W High', '₪'+fmt.num(stock.w52h,1)],
          ['52W Low', '₪'+fmt.num(stock.w52l,1)],
          ['Vol', fmt.vol(stock.vol)],
          ['ADV (ILS)', fmt.mc(stock.mc*0.001)],
          ['Range', rangePct + '%'],
          ['Float', '78.4%'],
          ['Inst.', '62.1%'],
          ['Insider', '11.4%'],
        ]}/>
      </div>

      <div className="d-chart-block">
        <div className="d-chart-h">
          <div className="panel-t">PRICE CHART</div>
          <div className="seg seg-sm">
            {['5D','1M','3M','6M','1Y','3Y'].map(p => <button key={p} className={'seg-btn ' + (p==='3M' && 'is-active')}>{p}</button>)}
          </div>
          <div className="grow"/>
          <div className="seg seg-sm">
            <button className="seg-btn is-active">₪</button>
            <button className="seg-btn">%</button>
          </div>
          <span className="d-chart-tag t-down mono">3M: -4.25%</span>
        </div>
        <div className="chart-wrap chart-wrap-tall">
          <CandleChart seed={stock.t.charCodeAt(0)} bars={64} trend={-8} height={260}/>
        </div>
      </div>
    </div>
  );
}

function StatBlock({title, rows}) {
  return (
    <div className="stat-blk">
      <div className="stat-blk-h">{title}</div>
      <div className="stat-rows">
        {rows.map(([k,v], i) => (
          <div key={i} className="stat-row">
            <span className="sr-k">{k}</span>
            <span className="sr-v mono">{v}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

function StatsTab({stock, ccy}) {
  const sections = [
    {title: 'VALUATION', rows: [
      ['P/E (TTM)', stock.pe?.toFixed(1)+'x', 'FWD P/E', stock.fpe?.toFixed(1)+'x'],
      ['EV/EBITDA', stock.ev != null ? stock.ev.toFixed(1)+'x' : '—', 'EV/FCF', stock.evfcf != null ? stock.evfcf.toFixed(1)+'x' : '—'],
      ['P/S', stock.ps?.toFixed(1)+'x', 'P/B', stock.pb?.toFixed(1)+'x'],
      ['MKT CAP', fmt.mc(stock.mc), 'ENT. VALUE', '—'],
      ['DIV YIELD', stock.div != null ? stock.div.toFixed(2)+'%' : '—', 'EPS (TTM)', '₪'+stock.eps?.toFixed(2)],
      ['BETA', stock.beta?.toFixed(2), 'IPO DATE', '2000-02-01'],
    ]},
    {title: 'PROFITABILITY & RETURNS', rows: [
      ['ROIC', '—', 'WACC', '7.8%'],
      ['ROE', <Delta v={stock.roe} d={1}/>, 'ROA', <Delta v={stock.roe*0.1} d={1}/>],
      ['GROSS MARGIN', stock.gm + '%', 'OP MARGIN', <Delta v={stock.opm} d={1}/>],
      ['NET MARGIN', <Delta v={stock.nm} d={1}/>, 'EBITDA MARGIN', '—'],
      ['REV GROWTH', <Delta v={stock.rev} d={1}/>, 'EPS GROWTH', <Delta v={stock.rev*0.5} d={1}/>],
    ]},
    {title: 'FINANCIAL HEALTH & LIQUIDITY', rows: [
      ['DEBT / EQUITY', stock.dscr?.toFixed(2) ?? '—', 'CURRENT RATIO', '—'],
      ['QUICK RATIO', stock.qr?.toFixed(2) ?? '—', 'ADV (ILS)', fmt.mc(stock.mc*0.001)],
      ['TOTAL DEBT', '—', 'CASH & EQUIV', '—'],
    ]},
    {title: 'MARKET DATA', rows: [
      ['52W HIGH', '₪'+fmt.num(stock.w52h,1), '52W LOW', '₪'+fmt.num(stock.w52l,1)],
      ['VOLUME', fmt.vol(stock.vol), 'MKT CAP', fmt.mc(stock.mc)],
      ['EXCHANGE', <span><span className="t-fg">TASE</span> · Tel Aviv</span>, 'CURRENCY', 'ILS (₪)'],
    ]},
  ];
  return (
    <div className="d-tab d-tab-stats">
      <div className="stats-h">STATISTICS</div>
      {sections.map(s => (
        <div key={s.title} className="stats-sect">
          <div className="stats-sect-h">{s.title}</div>
          <div className="stats-grid">
            {s.rows.map((r, i) => (
              <React.Fragment key={i}>
                <div className="stats-cell">
                  <span className="sc-k">{r[0]}</span>
                  <span className="sc-v mono">{r[1]}</span>
                </div>
                <div className="stats-cell">
                  <span className="sc-k">{r[2]}</span>
                  <span className="sc-v mono">{r[3]}</span>
                </div>
              </React.Fragment>
            ))}
          </div>
        </div>
      ))}
    </div>
  );
}

function FinancialsTab({stock}) {
  const years = [2021, 2022, 2023, 2024];
  const incomeRows = [
    ['Revenue', [10250, 13240, 14740, 14670]],
    ['Cost of Revenue', [-5120, -6420, -6890, -6520]],
    ['Gross Profit', [5130, 6820, 7850, 8150]],
    ['Operating Income', [3850, 4920, 5670, 5980]],
    ['Net Income', [3190, 4470, 4910, 5460]],
    ['EPS Diluted', [12.40, 17.52, 19.61, 21.22]],
    ['Net Interest Income', [7680, 10240, 11970, 11810]],
    ['Interest Expense', [2870, 5960, 12030, 13980]],
    ['Tax Effect', [620, 1255, 0, 0]],
  ];
  const balanceRows = [
    ['Total Assets', [342500, 365400, 384200, 401800]],
    ['Total Equity', [20770, 23780, 27460, 31290]],
    ['Total Debt', [38660, 34070, 37810, 37580]],
    ['Total Liabilities', [370540, 403420, 419500, 452910]],
    ['Cash & Equivalents', [54200, 61400, 58200, 55400]],
    ['Retained Earnings', [17500, 20680, 24200, 27780]],
  ];
  const cashflowRows = [
    ['Operating CF', [9250, 11400, 12800, 13900]],
    ['Investing CF', [-25120, -34400, -20460, -43350]],
    ['Financing CF', [30240, 28240, 9840, 25080]],
    ['Free Cash Flow', [3790, 3510, 3090, 13770]],
    ['CapEx', [-336, -391, -438, -606]],
    ['Dividends Paid', [-1250, -956, -1390, -1900]],
    ['Change in Cash', [9250, -2260, -7090, -3890]],
  ];
  return (
    <div className="d-tab d-tab-fin">
      <div className="fin-toggle">
        <div className="seg seg-sm">
          <button className="seg-btn is-active">Annual</button>
          <button className="seg-btn">Quarterly</button>
        </div>
        <div className="grow"/>
        <span className="t-dim">All figures in ILS millions</span>
      </div>

      <div className="rev-chart">
        <div className="panel-t">REVENUE TREND</div>
        <div className="rev-bars">
          {[10250, 13240, 14740, 14670].map((v, i) => (
            <div key={i} className="rev-bar-wrap">
              <div className="rev-v mono">{(v/1000).toFixed(2)}B</div>
              <div className="rev-bar" style={{height: (v/16000)*100 + '%'}}/>
              <div className="rev-y mono">{years[i]}</div>
            </div>
          ))}
          <svg className="rev-line" viewBox="0 0 400 100" preserveAspectRatio="none">
            <polyline points="50,72 150,52 250,42 350,40" fill="none" stroke="var(--accent-2)" strokeWidth="2" vectorEffect="non-scaling-stroke"/>
            {[[50,72],[150,52],[250,42],[350,40]].map((p,i)=>(
              <circle key={i} cx={p[0]} cy={p[1]} r="3" fill="var(--accent-2)"/>
            ))}
          </svg>
        </div>
      </div>

      <div className="fin-grid">
        <FinTable title="INCOME STATEMENT" years={years} rows={incomeRows}/>
        <FinTable title="BALANCE SHEET" years={years} rows={balanceRows}/>
        <FinTable title="CASH FLOW" years={years} rows={cashflowRows}/>
      </div>

      <div className="filing-block">
        <div className="filing-t">
          <a className="link">View regulatory filings on Maya (<Hebrew>מאיה</Hebrew>) — {stock.t}</a>
        </div>
        <div className="filing-s">Maya · TASE Regulatory Filings</div>
        <div className="filing-d">Official TASE disclosure system. Click to view all regulatory filings, immediate reports, and financial statements for this company.</div>
      </div>
    </div>
  );
}

function FinTable({title, years, rows}) {
  return (
    <div className="fin-tbl">
      <div className="fin-tbl-h">{title}</div>
      <div className="fin-grid-row fin-grid-h">
        <div>METRIC</div>
        {years.map(y => <div key={y} className="mono">{y}</div>)}
      </div>
      {rows.map(([metric, vals], i) => (
        <div key={metric} className={'fin-grid-row ' + (i%2 ? 'fin-z' : '')}>
          <div className="fin-metric">{metric}</div>
          {vals.map((v, j) => {
            const prev = j > 0 ? vals[j-1] : null;
            const ch = prev != null && prev !== 0 ? ((v - prev)/Math.abs(prev))*100 : null;
            return (
              <div key={j} className="fin-cell mono">
                <span>{v >= 1000 ? (v/1000).toFixed(2)+'B' : v.toFixed(0)+'M'}</span>
                {ch != null && Math.abs(ch) > 0.5 && (
                  <span className={'fin-ch ' + (ch >= 0 ? 't-up' : 't-down')}>{ch>0?'+':''}{ch.toFixed(0)}%</span>
                )}
              </div>
            );
          })}
        </div>
      ))}
    </div>
  );
}

function NewsTab({stock}) {
  const news = [
    {d: '2026-05-09', src: 'Globes', t: stock.n + ' reports Q1 net profit of ₪1.42B, beating estimates', cat: 'EARNINGS'},
    {d: '2026-05-07', src: 'TheMarker', t: 'Analysts raise price target on improving NIM outlook', cat: 'ANALYST'},
    {d: '2026-05-05', src: 'Calcalist', t: 'Board approves ₪450M share buyback authorization', cat: 'CAPITAL'},
    {d: '2026-05-02', src: 'Maya Filing', t: 'Immediate report: Senior management appointment', cat: 'FILING', he: true},
    {d: '2026-04-28', src: 'Reuters', t: 'Israeli banks face new BoI capital requirements; sector reaction', cat: 'SECTOR'},
    {d: '2026-04-21', src: 'Globes', t: 'Branch network rationalization continues — 14 closures Q2', cat: 'OPERATIONS'},
  ];
  return (
    <div className="d-tab d-tab-news">
      <div className="stats-h">NEWS &amp; FILINGS</div>
      <div className="news-list">
        {news.map((n, i) => (
          <div key={i} className="news-row">
            <div className="news-d mono">{n.d}</div>
            <div className="news-src">{n.src}</div>
            <Pill tone="muted">{n.cat}</Pill>
            <div className="news-t">{n.he && <Hebrew>הודעה מיידית: </Hebrew>}{n.t}</div>
          </div>
        ))}
      </div>
    </div>
  );
}

function PeersTab({stock}) {
  const peers = window.TASE_DATA.stocks.filter(x => x.s === stock.s).slice(0, 8);
  return (
    <div className="d-tab d-tab-peers">
      <div className="stats-h">PEER COMPARISON · {stock.s}</div>
      <div className="peer-tbl mono-tbl" style={{['--cols']:'72px 1.6fr 90px 80px 100px 80px 80px 80px 80px 60px'}}>
        <div className="tr tr-h">
          <div className="th">TICKER</div>
          <div className="th th-l">COMPANY</div>
          <div className="th c-num">MKT CAP</div>
          <div className="th c-num">P/E</div>
          <div className="th c-num">EV/EBITDA</div>
          <div className="th c-num">ROE</div>
          <div className="th c-num">NET MGN</div>
          <div className="th c-num">REV Δ</div>
          <div className="th c-num">D/E</div>
          <div className="th c-num">β</div>
        </div>
        {peers.map((p,i) => (
          <div key={p.t} className={'tr ' + (i%2 ? 'tr-z' : '') + (p.t===stock.t ? ' tr-self' : '')}>
            <div className="td c-tick mono">{p.t}</div>
            <div className="td c-name">{p.n}</div>
            <div className="td c-num mono">{fmt.mc(p.mc)}</div>
            <div className="td c-num mono">{p.pe?.toFixed(1)}x</div>
            <div className="td c-num mono">{p.ev != null ? p.ev.toFixed(1)+'x' : <span className="t-dim">—</span>}</div>
            <div className="td c-num mono"><Delta v={p.roe} d={1}/></div>
            <div className="td c-num mono"><Delta v={p.nm} d={1}/></div>
            <div className="td c-num mono"><Delta v={p.rev} d={1}/></div>
            <div className="td c-num mono">{p.dscr?.toFixed(2) ?? '—'}</div>
            <div className="td c-num mono">{p.beta?.toFixed(2)}</div>
          </div>
        ))}
      </div>
    </div>
  );
}
