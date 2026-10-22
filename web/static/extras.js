/* 股票健診：延伸資料（集保大戶、內部人、國際連動、同業、新聞）與績效追蹤頁 */
"use strict";

const toneTag = t => t > 0 ? '<span class="pill p-bull">利多</span>' : t < 0 ? '<span class="pill p-bear">利空</span>' : '<span class="pill p-neu">中性</span>';
const noteList = ns => (ns && ns.length) ? `<ul class="notes" style="padding-left:0">${ns.map(([p, t]) => `<li class="${p > 0 ? "pos" : "neg"}"><b>${p > 0 ? "▲" : "▼"}</b><span>${esc(t)}</span></li>`).join("")}</ul>` : "";
const refTag = '<span class="tag">參考，不計分</span>';
const errBox = (x, what) => x && x.error ? `<p class="small muted">${what}暫時無法取得（${esc(x.error)}）</p>` : "";

function loadExtras(a) {
  const el = $("#extras"); if (!el) return;
  el.innerHTML = `<h2 class="sec-title">大戶、內部人、國際連動與新聞 <span class="muted">正在整理延伸資料，第一次約需 10~20 秒…</span></h2>
    <div class="grid2"><div class="skel" style="height:240px"></div><div class="skel" style="height:240px"></div></div>`;
  api(`/api/stock/${encodeURIComponent(a.code)}/extra`)
    .then(x => {
      const e = $("#extras"); if (!e) return;
      e.innerHTML = extrasHtml(a, x);
      $$("tr[data-code]", e).forEach(tr => tr.onclick = () => location.hash = `#/stock/${tr.dataset.code}`);
    })
    .catch(e => { const x = $("#extras"); if (x) x.innerHTML = `<div class="err">延伸資料暫時無法取得：${esc(e.message)}</div>`; });
}

function extrasHtml(a, x) {
  const left = [], right = [];
  if (x.holders !== undefined) left.push(`<div class="card"><h3>大戶籌碼（集保） ${refTag}</h3>${holdersHtml(x.holders)}</div>`);
  if (x.events !== undefined) left.push(`<div class="card"><h3>內部人與公司 ${refTag}</h3>${eventsHtml(x.events)}</div>`);
  if (x.linkage !== undefined) right.push(`<div class="card"><h3>國際連動：美股前一晚 → 台股隔天 ${refTag}</h3>${linkHtml(a, x.linkage)}</div>`);
  if (x.peers !== undefined) right.push(`<div class="card"><h3>同業族群 ${refTag}</h3>${peersHtml(a, x.peers)}</div>`);
  const newsCard = `<div class="card"><h3>新聞與公告 ${refTag}</h3>${newsHtml(x.news, x.events)}</div>`;
  return `<h2 class="sec-title">大戶、內部人、國際連動與新聞 <span class="muted">這些資料先給你參考；經過回測驗證有效的部分才會納入健診分數</span></h2>
    ${left.length || right.length ? `<div class="grid2"><div class="stack">${left.join("")}</div><div class="stack">${right.join("")}</div></div>` : ""}
    <div style="margin-top:16px">${newsCard}</div>`;
}

function holdersHtml(h) {
  if (!h || h.error || !h.weeks) return errBox(h, "集保資料") || '<p class="muted">暫無集保資料。</p>';
  const w = h.weeks, last = w[w.length - 1];
  const vals = w.map(x => x.big1000), lo = Math.min(...vals), hi = Math.max(...vals), r = (hi - lo) || 1;
  const bars = w.map(x => `<div class="fb" title="${esc(x.date)}：千張大戶 ${x.big1000}%"><div style="height:100%;display:flex;align-items:flex-end">
      <u style="height:${20 + (x.big1000 - lo) / r * 80}%;background:var(--accent)"></u></div></div>`).join("");
  return `<div class="inds">
      <div class="ind"><div class="il">千張大戶持股</div><div class="iv num">${nf(last.big1000, 2)}%</div><div class="ie">${w.length} 週變化 <b class="${cls(h.change_big1000)}">${h.change_big1000 > 0 ? "+" : ""}${nf(h.change_big1000, 2)}</b> 個百分點</div></div>
      <div class="ind"><div class="il">400 張以上大戶</div><div class="iv num">${nf(last.big400, 2)}%</div><div class="ie">中大型投資人合計</div></div>
      <div class="ind"><div class="il">50 張以下散戶</div><div class="iv num">${nf(last.retail50, 2)}%</div><div class="ie">散戶比例越低，籌碼越集中</div></div>
      <div class="ind"><div class="il">股東人數</div><div class="iv num">${last.holders.toLocaleString()}</div><div class="ie">${w.length} 週 <b class="${cls(-h.change_holders)}">${pct(h.change_holders)}</b>（人數減少代表集中）</div></div>
    </div>
    <div class="small muted" style="margin-top:10px">近 ${w.length} 週千張大戶持股比例（每週五公布）</div>
    <div class="flowbars" style="height:70px">${bars}</div>
    <div class="fb-lab">${w.map(x => `<span>${esc(x.date.slice(4, 6))}/${esc(x.date.slice(6))}</span>`).join("")}</div>
    ${noteList(h.notes)}`;
}

function eventsHtml(e) {
  if (!e || e.error) return errBox(e, "內部人資料") || "";
  const ins = e.insider || [];
  const rows = ins.slice(0, 8).map(r => `<tr><td>${esc(r.date.slice(5))}</td><td class="l">${esc(r.role)}</td><td class="l">${r.kind === "市場賣出" ? '<span class="dn">市場賣出</span>' : esc(r.kind)}</td>
    <td class="num">${nf(r.lots, 0)}</td><td class="num">${r.pct_of_holding != null ? pct(r.pct_of_holding, 1, false) : "—"}</td></tr>`).join("");
  return `${noteList(e.notes)}
    <div class="inds" style="margin-bottom:10px">
      <div class="ind"><div class="il">董監質押比例</div><div class="iv num ${e.pledge >= 30 ? "dn" : ""}">${e.pledge != null ? nf(e.pledge, 1) + "%" : "未列入"}</div><div class="ie">質押高的公司，股價大跌時可能被斷頭賣出</div></div>
      <div class="ind"><div class="il">董事持股成數</div><div class="iv">${e.director_shortfall ? '<span class="dn">低於法定</span>' : "正常"}</div><div class="ie">董事持股太少，和股東利益較不一致</div></div>
    </div>
    <div class="small muted">內部人持股轉讓申報（自 ${esc(e.history_since)} 起累積）</div>
    ${rows ? `<div class="tbl-wrap"><table class="tbl"><thead><tr><th>日期</th><th class="l">身分</th><th class="l">方式</th><th>張數</th><th>占其持股</th></tr></thead><tbody>${rows}</tbody></table></div>`
      : '<p class="muted small">期間內沒有內部人申報轉讓。</p>'}
    ${e.major_holders && e.major_holders.length ? `<p class="small" style="margin:10px 0 0"><b>持股超過 10% 的大股東：</b>${e.major_holders.map(esc).join("、")}</p>` : ""}`;
}

function linkHtml(a, L) {
  if (!L || L.error || !L.rows) return errBox(L, "連動資料") || "";
  const b = L.best;
  let lead = '<p class="muted">沒有找到穩定的美股連動（前後兩段期間都成立才算），這檔比較不受美股前一晚影響。</p>';
  if (b) {
    const e = L.expect || {};
    lead = `<div class="advice"><b>${esc(b.name)}</b> 和 ${esc(a.name)} 的隔夜連動最穩定：過去 3 年，${esc(b.name)} 前一晚每漲 1%，${esc(a.name)} 隔天平均 ${b.beta >= 0 ? "漲" : "跌"} <b>${nf(Math.abs(b.beta), 2)}%</b>
      （相關係數 ${nf(b.corr, 2)}，最近 30% 期間驗證仍有 ${nf(b.corr_test, 2)}）。<br>
      ${esc(b.name)} 最新一天（${esc(b.last_date)}）<b class="${cls(b.last_ret)}">${pct(b.last_ret, 2)}</b>，
      ${e.pending ? `<b>下一個交易日</b>依歷史關係推估約 <b class="${cls(e.move)}">${pct(e.move, 2)}</b>。` : "這個影響已經反映在台股最新一天的股價上。"}</div>`;
  }
  const rows = L.rows.map(r => `<tr><td class="l">${esc(r.name)}</td><td class="num">${nf(r.corr, 2)}</td><td class="num">${nf(r.corr_test, 2)}</td><td class="num">${nf(r.beta, 2)}</td>
    <td class="num ${cls(r.last_ret)}">${pct(r.last_ret, 2)}</td><td>${r.valid ? '<span class="up">✓</span>' : '<span class="muted">—</span>'}</td></tr>`).join("");
  return `${lead}<div class="tbl-wrap" style="margin-top:10px"><table class="tbl"><thead><tr><th class="l">美股</th><th>相關係數</th><th>驗證期</th><th>連動倍數</th><th>最新漲跌</th><th>有效</th></tr></thead><tbody>${rows}</tbody></table></div>
    <p class="small muted" style="margin:8px 0 0">做法：用台股每天的漲跌，對比美股「前一個交易日」的漲跌（美股收盤在台股開盤前）。前 70% 期間估計、後 30% 期間驗證，兩段都成立才打 ✓。</p>`;
}

function peersHtml(a, P) {
  if (!P || P.error || !P.rows) return errBox(P, "同業資料") || '<p class="muted">找不到同業資料。</p>';
  const me = P.me || {};
  const rows = P.rows.map(r => `<tr class="click" data-code="${esc(r.code)}"><td class="stk"><b>${esc(r.name)}</b><span>${esc(r.code)}</span></td><td class="num">${nf(r.corr, 2)}</td>
    <td class="num ${cls(r.ret20)}">${pct(r.ret20)}</td><td class="num ${cls(r.ret60)}">${pct(r.ret60)}</td><td class="num ${cls(r.mom12_1)}">${pct(r.mom12_1, 0)}</td></tr>`).join("");
  return `<p style="margin-top:0">產業：<b>${esc(P.industry)}</b>。同業中期動能中位數 <b class="${cls(P.industry_mom)}">${pct(P.industry_mom, 0)}</b>、近 60 日中位數 <b class="${cls(P.industry_ret60)}">${pct(P.industry_ret60)}</b>；
    ${esc(a.name)} 近 60 日 <b class="${cls(me.ret60)}">${pct(me.ret60)}</b>，在 ${P.count} 檔中排第 <b>${P.rank60}</b>。</p>
    <div class="tbl-wrap"><table class="tbl"><thead><tr><th>同業（成交額前 10）</th><th>連動</th><th>20 日</th><th>60 日</th><th>中期動能</th></tr></thead><tbody>${rows}</tbody></table></div>
    <p class="small muted" style="margin:8px 0 0">連動＝近一年每日漲跌的相關係數，越接近 1 走勢越像。點股票可以看它的健診。</p>`;
}

function newsHtml(n, e) {
  let h = "";
  if (n && !n.error && n.items) {
    h += `<p style="margin-top:0">近兩週新聞標題判讀：<b class="up">利多 ${n.pos}</b> 則、<b class="dn">利空 ${n.neg}</b> 則，整體 ${ratingPill(n.mood)}
      <span class="small muted">（只看標題關鍵字，快速掃描用；新聞通常 1~3 天內就反映在股價）</span></p>
      <div class="news">${n.items.map(it => `<a class="nw" href="${esc(it.link)}" target="_blank" rel="noopener">${toneTag(it.tone)}<span class="nt">${esc(it.title)}</span>
        <span class="ns">${esc(it.source)} · ${esc(it.time)}${it.pos.length || it.neg.length ? ` · 關鍵字：${[...it.pos, ...it.neg].map(esc).join("、")}` : ""}</span></a>`).join("") || '<p class="muted">近兩週沒有相關新聞。</p>'}</div>`;
  } else h += errBox(n, "新聞");
  const anns = (e && e.announcements) || [];
  if (anns.length) h += `<h4>公司重大訊息</h4><div class="news">${anns.slice(0, 10).map(r => `<div class="nw"><span class="pill p-neu">公告</span><span class="nt">${esc(r.subject)}</span><span class="ns">${esc(r.date)} ${esc(r.time)}</span></div>`).join("")}</div>`;
  return h;
}

// ---------------------------------------------------------------- 績效追蹤頁
async function viewTrack(app) {
  document.title = "績效追蹤｜股票健診";
  app.innerHTML = `<h1 style="font-size:26px;margin:8px 0 6px">績效追蹤：建議準不準？</h1>
    <p class="muted" style="margin:0 0 16px">每個交易日晚上，系統會自動記錄約 90 檔股票的健診結果，之後再回頭檢查：當初建議買進的，後來真的有比大盤好嗎？</p>
    <div id="trLive">${loading("載入實際紀錄…")}</div><div id="trSim"></div>`;
  try {
    const t = await api("/api/track");
    const s = t.summary || {};
    const cell = (b, h) => b[`ex${h}`] == null ? `<td class="muted">累積中</td><td class="muted">—</td>` :
      `<td class="num ${cls(b[`ex${h}`])}">${pct(b[`ex${h}`])}</td><td class="num">${pct(b[`win${h}`], 0, false)}<span class="small muted">（${b[`n${h}`]}）</span></td>`;
    const rows = (s.buckets || []).map(b => `<tr><td class="l"><b>${esc(b.action)}</b></td><td class="num">${b.n}</td>
      <td class="num ${cls(b.ex_now)}">${pct(b.ex_now, 2)}</td>${cell(b, 5)}${cell(b, 20)}${cell(b, 60)}
      <td class="num">${pct(b.stop_rate, 0, false)}</td><td class="num">${pct(b.target_rate, 0, false)}</td></tr>`).join("");
    const picks = (t.picks || []).map(p => `<tr class="click" data-code="${esc(p.code)}"><td>${esc(p.date)}</td><td class="stk l"><b>${esc(p.name)}</b><span>${esc(p.code)}</span></td>
      <td class="l">${esc(p.action)}</td><td class="num">${px(p.price)}</td><td class="num">${px(p.now)}</td>
      <td class="num ${cls(p.ret_now)}">${pct(p.ret_now)}</td><td class="num ${cls(p.excess_now)}">${pct(p.excess_now)}</td>
      <td>${p.hit === "停損" ? '<span class="pill p-bear">觸及停損</span>' : p.hit === "目標" ? '<span class="pill p-bull">達到目標</span>' : '<span class="muted small">進行中</span>'}</td></tr>`).join("");
    $("#trLive").innerHTML = `<div class="card"><h3>上線後的實際紀錄 <span class="muted">${esc(s.first || "")} ～ ${esc(s.last || "")}，共 ${s.days || 0} 個交易日、${s.n || 0} 筆建議；更新於 ${esc(t.generated)}</span></h3>
      <div class="tbl-wrap"><table class="tbl"><thead><tr><th class="l">當時的建議</th><th>筆數</th><th>至今比大盤多賺</th><th>5 日超額</th><th>勝率</th><th>20 日超額</th><th>勝率</th><th>60 日超額</th><th>勝率</th><th>觸及停損</th><th>達到目標</th></tr></thead>
      <tbody>${rows}</tbody></table></div>
      <p class="small muted" style="margin:8px 0 0">「超額」＝個股報酬減去同期大盤報酬；「勝率」＝跑贏大盤的比例（括號是樣本數）。紀錄剛開始累積，5 日要一週、20 日要一個月、60 日要三個月才會有數字。</p></div>
      <div class="card"><h3>最近的買進建議 <span class="muted">每檔取第一次出現買進／布局建議的那天</span></h3>
      <div class="tbl-wrap"><table class="tbl"><thead><tr><th>建議日</th><th class="l">股票</th><th class="l">建議</th><th>當時價</th><th>現價</th><th>報酬</th><th>比大盤</th><th>狀態</th></tr></thead><tbody>${picks || '<tr><td colspan="8" class="empty">尚無資料</td></tr>'}</tbody></table></div></div>`;
    $$("tr[data-code]", $("#trLive")).forEach(tr => tr.onclick = () => location.hash = `#/stock/${tr.dataset.code}`);
  } catch (e) { $("#trLive").innerHTML = `<div class="err">實際紀錄暫時無法取得：${esc(e.message)}</div>`; }
  try {
    const m = await api("/api/model");
    const el = $("#trSim"); if (!el) return;
    el.innerHTML = m.full ? simHtml(m.full) : "";
  } catch (e) { /* 沒有模擬資料就不顯示 */ }
}

function simHtml(F) {
  const u = F.universe;
  const rows = (F.ratings || []).map(b => `<tr><td class="l"><b>${esc(b.action)}</b></td><td class="num">${b.n.toLocaleString()}</td>
    <td class="num ${cls(b.ex20)}">${pct(b.ex20)}</td><td class="num">${pct(b.win20, 0, false)}</td><td class="num ${cls(b.ex60)}">${pct(b.ex60)}</td><td class="num">${pct(b.win60, 0, false)}</td></tr>`).join("");
  return `<div class="card"><h3>歷史模擬：如果過去 4 年都照建議做 <span class="muted">${esc(u.start)} ～ ${esc(u.end)}，每月底取樣，${u.stocks.toLocaleString()} 檔股票（含 ${u.delisted} 檔已下市）</span></h3>
    <div class="tbl-wrap"><table class="tbl"><thead><tr><th class="l">當時的建議</th><th>樣本數</th><th>之後 20 日比大盤</th><th>勝率</th><th>之後 60 日比大盤</th><th>勝率</th></tr></thead><tbody>${rows}</tbody></table></div>
    <p class="small muted" style="margin:8px 0 0">用當時就能取得的資料重算健診分數（營收只用已公布的月份、價格只用當天以前），並納入後來下市的股票，避免「只看活下來的股票」造成的高估。完整說明見「怎麼看懂」。</p></div>`;
}

// ---------------------------------------------------------------- 說明頁：完整模型驗證
function fullCardHtml(F) {
  const u = F.universe, ic = F.ic || {};
  const names = { trend: "趨勢", momentum: "動能", volume: "量價", risk: "風險控管", tech: "技術總分", fundamental: "基本面", chips: "籌碼", composite: "健診總分", industry_mom: "產業動能" };
  const icCell = v => v ? `<td class="num ${cls(v.ic)}">${(v.ic > 0 ? "+" : "") + nf(v.ic, 3)}</td><td class="num">${v.t == null ? "—" : nf(v.t, 1)}</td>` : "<td>—</td><td>—</td>";
  const icRows = Object.keys(names).filter(k => ic["20"] && ic["20"][k]).map(k => `<tr><td class="l"><b>${names[k]}</b></td>${icCell(ic["20"][k])}${icCell((ic["60"] || {})[k])}</tr>`).join("");
  const q = (F.quintiles || {})["60"] || [], mx = Math.max(1e-9, ...q.map(Math.abs));
  const wRows = (F.weights || []).map(w => `<tr><td class="l">${esc(w.name)}</td><td class="num">${(w.ic20 > 0 ? "+" : "") + nf(w.ic20, 3)}</td><td class="num">${(w.ic60 > 0 ? "+" : "") + nf(w.ic60, 3)}</td><td class="num ${cls(w.spread60)}">${pct(w.spread60)}</td></tr>`).join("");
  const s = F.survivorship || {}, b = F.big_holder;
  return `<h3>完整模型驗證（含已下市股票） <span class="muted">${u.stocks.toLocaleString()} 檔、其中 ${u.delisted} 檔已下市；${esc(u.start)} ～ ${esc(u.end)} 每月底取樣，共 ${u.rows.toLocaleString()} 筆</span></h3>
    <p style="margin-top:0">用「當時就拿得到」的資料重算每一個月底的健診分數：股價只用當天以前、月營收只用已公布的月份、法人與融資用當天以前 10 個交易日。股票池是當時有在交易、上市滿一年、每天平均成交 2,000 萬元以上的所有股票。</p>
    <p><b>健診總分</b>依高低分 5 組，之後 60 日相對報酬：</p>
    <div class="qbars">${q.map(v => `<div class="qb"><b class="${cls(v)}">${pct(v)}</b><u class="${v < 0 ? "neg" : ""}" style="height:${Math.abs(v) / mx * 75}%;background:${v >= 0 ? "var(--up)" : "var(--dn)"}"></u></div>`).join("")}</div>
    <div class="qlabs"><span>分數最低 20%</span><span>第 2 組</span><span>第 3 組</span><span>第 4 組</span><span>分數最高 20%</span></div>
    <h4>各面向的預測力（IC：越大越好，t 值 ≥ 2 代表統計上可信）</h4>
    <div class="tbl-wrap"><table class="tbl"><thead><tr><th class="l">面向</th><th>20 日 IC</th><th>t 值</th><th>60 日 IC</th><th>t 值</th></tr></thead><tbody>${icRows}</tbody></table></div>
    <h4>權重怎麼決定的</h4>
    <div class="tbl-wrap"><table class="tbl"><thead><tr><th class="l">權重組合</th><th>20 日 IC</th><th>60 日 IC</th><th>最高 vs 最低組 60 日差距</th></tr></thead><tbody>${wRows}</tbody></table></div>
    <h4>存活者偏差有多大</h4>
    <p style="margin-top:0">如果只用「今天還在交易」的股票回測，平均 60 日報酬是 <b>${pct(s.mean_survivors)}</b>，納入已下市股票後是 <b>${pct(s.mean_all)}</b>；
      高分組的 60 日報酬則從 <b>${pct(s.top_q_survivors)}</b> 變成 <b>${pct(s.top_q_all)}</b>。這就是只看「活下來的股票」會讓回測看起來比較漂亮的原因。</p>
    ${b ? `<h4>千張大戶持股變化有用嗎？</h4><p style="margin-top:0">集保只提供一年歷史，用成交最大的 ${b.stocks} 檔、${b.periods} 期（每 4 週）檢驗「大戶 4 週加碼幅度」對之後 4 週報酬的預測力：
      IC ${(b.ic > 0 ? "+" : "") + nf(b.ic, 3)}（t=${b.t == null ? "—" : nf(b.t, 1)}，${pct(b.hit, 0, false)} 的期間為正）。${Math.abs(b.t || 0) < 2 ? "樣本只有一年、統計上還不夠可信，所以先列為參考資訊、不計入分數。" : ""}</p>` : ""}`;
}
