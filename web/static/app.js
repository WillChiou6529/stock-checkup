/* 股票健診 前端（無框架、無建置步驟） */
"use strict";

// ---------------------------------------------------------------- 工具
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const nf = (v, d = 2) => v == null || isNaN(v) ? "—" : Number(v).toLocaleString("zh-TW", { minimumFractionDigits: d, maximumFractionDigits: d });
const px = v => v == null || isNaN(v) ? "—" : Math.abs(v) >= 1000 ? nf(v, 0) : nf(v, 2);
const pct = (v, d = 1, sign = true) => v == null || isNaN(v) ? "—" : (sign && v > 0 ? "+" : "") + (v * 100).toFixed(d) + "%";
const pp = (v, d = 1) => v == null || isNaN(v) ? "—" : (v > 0 ? "+" : "") + Number(v).toFixed(d) + "%"; // 已是百分比的數字
const cls = v => v == null || isNaN(v) || v === 0 ? "" : v > 0 ? "up" : "dn";
const sColor = s => s == null ? "var(--muted)" : s >= 60 ? "var(--up)" : s < 45 ? "var(--dn)" : "var(--neu)";
const ratingPill = r => `<span class="pill ${r === "偏多" ? "p-bull" : r === "偏空" ? "p-bear" : "p-neu"}">${esc(r)}</span>`;
const boardName = s => s.tw ? (s.etf ? "ETF" : s.board === "otc" ? "上櫃" : "上市") : "美股";

const store = {
  get(k, d) { try { const v = localStorage.getItem(k); return v ? JSON.parse(v) : d; } catch (e) { return d; } },
  set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch (e) { /* 私密模式 */ } },
};

async function api(path) {
  const r = await fetch(path);
  let j = null;
  try { j = await r.json(); } catch (e) { /* 非 JSON */ }
  if (!r.ok) throw new Error((j && (j.detail || j.error)) || `伺服器錯誤 ${r.status}`);
  return j;
}

let DEFAULTS = { watchlist: ["2330", "2317", "2454", "0050"], capital: 1000000, risk_per_trade_pct: 1, max_position_pct: 20 };
const watch = {
  list() { return store.get("watch", null) || DEFAULTS.watchlist.slice(); },
  has(c) { return this.list().includes(c); },
  toggle(c) { const l = this.list(); const i = l.indexOf(c); i >= 0 ? l.splice(i, 1) : l.unshift(c); store.set("watch", l); return i < 0; },
  remove(c) { store.set("watch", this.list().filter(x => x !== c)); },
};
const holds = {
  all() { return store.get("hold", {}); },
  get(c) { return this.all()[c]; },
  set(c, v) { const a = this.all(); v ? a[c] = v : delete a[c]; store.set("hold", a); },
};
const settings = () => Object.assign({ capital: DEFAULTS.capital, risk: DEFAULTS.risk_per_trade_pct, maxpos: DEFAULTS.max_position_pct }, store.get("set", {}));

function gauge(score, w = 132) {
  const c = Math.PI * 50, p = Math.max(0, Math.min(100, score || 0)) / 100;
  return `<svg viewBox="0 0 120 72" width="${w}" class="gauge" role="img" aria-label="健診分數 ${Math.round(score)}">
    <path d="M10 62 A50 50 0 0 1 110 62" class="g-bg"/>
    <path d="M10 62 A50 50 0 0 1 110 62" class="g-fg" style="stroke:${sColor(score)};stroke-dasharray:${(c * p).toFixed(1)} ${c.toFixed(1)}"/>
    <text x="60" y="56" text-anchor="middle" class="g-num" fill="${sColor(score)}">${Math.round(score)}</text>
    <text x="60" y="70" text-anchor="middle" class="g-lab">健診分數 / 100</text></svg>`;
}

function spark(arr, w = 110, h = 32) {
  const v = (arr || []).filter(x => x != null);
  if (v.length < 2) return "";
  const lo = Math.min(...v), hi = Math.max(...v), r = hi - lo || 1;
  const pts = v.map((x, i) => `${(i / (v.length - 1) * w).toFixed(1)},${(h - 2 - (x - lo) / r * (h - 4)).toFixed(1)}`).join(" ");
  const col = v[v.length - 1] >= v[0] ? "var(--up)" : "var(--dn)";
  return `<svg width="${w}" height="${h}" viewBox="0 0 ${w} ${h}" aria-hidden="true"><polyline points="${pts}" fill="none" stroke="${col}" stroke-width="1.6"/></svg>`;
}

const sbar = s => s == null ? '<span class="muted">—</span>' :
  `<span class="sbar"><i><u style="width:${s}%;background:${sColor(s)}"></u></i><b style="color:${sColor(s)}">${Math.round(s)}</b></span>`;

const loading = msg => `<div class="loading"><div class="spin"></div>${esc(msg)}</div>`;

// ---------------------------------------------------------------- 搜尋
function bindSearch(box, autofocus) {
  const input = $("input", box), sugg = $(".sugg", box);
  let items = [], idx = -1, timer = null, seq = 0;
  const close = () => { sugg.classList.remove("open"); idx = -1; };
  const go = code => { if (!code) return; input.value = ""; close(); input.blur(); location.hash = `#/stock/${encodeURIComponent(code.toUpperCase())}`; };
  const draw = () => {
    sugg.innerHTML = items.map((it, i) => `<div data-i="${i}" class="${i === idx ? "act" : ""}"><b>${esc(it.code)}</b><span>${esc(it.name)}</span><em>${esc(it.board)}</em></div>`).join("");
    sugg.classList.toggle("open", items.length > 0);
  };
  input.addEventListener("input", () => {
    clearTimeout(timer);
    const q = input.value.trim();
    if (!q) { items = []; draw(); return; }
    timer = setTimeout(async () => {
      const my = ++seq;
      try { const r = await api(`/api/search?q=${encodeURIComponent(q)}`); if (my === seq) { items = r; idx = items.length ? 0 : -1; draw(); } } catch (e) { /* 忽略 */ }
    }, 180);
  });
  input.addEventListener("keydown", e => {
    if (e.key === "ArrowDown") { idx = Math.min(idx + 1, items.length - 1); draw(); e.preventDefault(); }
    else if (e.key === "ArrowUp") { idx = Math.max(idx - 1, 0); draw(); e.preventDefault(); }
    else if (e.key === "Enter") { go(idx >= 0 && items[idx] ? items[idx].code : input.value.trim()); }
    else if (e.key === "Escape") close();
  });
  sugg.addEventListener("mousedown", e => { const d = e.target.closest("[data-i]"); if (d) { e.preventDefault(); go(items[+d.dataset.i].code); } });
  input.addEventListener("blur", () => setTimeout(close, 120));
  if (autofocus && window.innerWidth > 760) input.focus();
}

// ---------------------------------------------------------------- 主題
function applyTheme() {
  const t = store.get("theme", null);
  if (t) document.documentElement.dataset.theme = t; else delete document.documentElement.dataset.theme;
}
$("#themeBtn").addEventListener("click", () => {
  const dark = document.documentElement.dataset.theme ? document.documentElement.dataset.theme === "dark" : matchMedia("(prefers-color-scheme: dark)").matches;
  store.set("theme", dark ? "light" : "dark");
  applyTheme();
  route();
});
applyTheme();

// ---------------------------------------------------------------- 暖機提示
async function pollWarm() {
  const el = $("#warm");
  try {
    const s = await api("/api/health");
    if (s.ready) { el.hidden = true; return; }
    el.hidden = false;
    el.textContent = `伺服器剛啟動，正在下載今日資料（${s.stage} ${s.pct}%）。這段期間查詢會比較慢，約 1 分鐘後恢復正常。`;
  } catch (e) {
    el.hidden = false;
    el.textContent = "正在連線伺服器…（免費主機閒置後需要約 30~60 秒喚醒）";
  }
  setTimeout(pollWarm, 3000);
}

// ---------------------------------------------------------------- 路由
let charts = [];
function clearCharts() { charts.forEach(c => { try { c.remove(); } catch (e) { /* 已移除 */ } }); charts = []; }

function route() {
  clearCharts();
  const [, page = "", arg = ""] = (location.hash.slice(1) || "/").split("/");
  $$(".nav a").forEach(a => a.classList.toggle("on", a.dataset.nav === (page || "home")));
  const app = $("#app");
  window.scrollTo(0, 0);
  if (page === "stock" && arg) return viewStock(app, decodeURIComponent(arg).toUpperCase());
  if (page === "scan") return viewScan(app);
  if (page === "about") return viewAbout(app);
  return viewHome(app);
}
window.addEventListener("hashchange", route);

// ---------------------------------------------------------------- 首頁
function viewHome(app) {
  document.title = "股票健診｜30 秒看懂一檔股票";
  app.innerHTML = `
  <section class="hero">
    <h1>幫你的股票做個健診</h1>
    <p>輸入代號，30 秒看懂：<b>現在能不能買、在哪買、買多少、錯了在哪停損</b>。</p>
    <div class="search hero-search"><input type="search" placeholder="輸入代號或名稱，例如 2330、台積電、NVDA" autocomplete="off" aria-label="搜尋股票"><div class="sugg"></div></div>
    <div class="chips">${["2330", "2317", "2454", "0050", "2881", "NVDA"].map(c => `<button class="chip" data-go="${c}">${c}</button>`).join("")}</div>
  </section>
  <h2 class="sec-title">大盤概況 <span class="muted" id="mkTime"></span></h2>
  <div class="mk-row" id="mk"><div class="skel"></div><div class="skel"></div><div class="skel"></div></div>
  <h2 class="sec-title">我的自選股 <span class="muted">點股票看完整健診；在個股頁按「☆ 加入自選」新增</span></h2>
  <div class="card" id="wl">${loading("正在分析自選股…")}</div>
  <h2 class="sec-title">今日選股 <span class="muted">法人連續買超＋營收成長＋估值合理</span> <a href="#/scan" class="small">看全部 →</a></h2>
  <div class="cards" id="picks"><div class="skel" style="height:160px"></div><div class="skel" style="height:160px"></div><div class="skel" style="height:160px"></div></div>`;
  bindSearch($(".hero-search", app), true);
  $$("[data-go]", app).forEach(b => b.onclick = () => location.hash = `#/stock/${b.dataset.go}`);
  loadMarket();
  loadWatch();
  api("/api/scan").then(r => { const el = $("#picks"); if (el) el.innerHTML = r.rows.slice(0, 3).map(pickCard).join("") || '<div class="empty">今日沒有符合條件的股票</div>'; bindPicks(); })
    .catch(e => { const el = $("#picks"); if (el) el.innerHTML = `<div class="err">${esc(e.message)}</div>`; });
}

async function loadMarket() {
  try {
    const m = await api("/api/market");
    const el = $("#mk"); if (!el) return;
    $("#mkTime").textContent = `更新於 ${m.updated}`;
    const regimeCls = r => r === "多頭" ? "p-bull" : r === "空頭" ? "p-bear" : "p-neu";
    let h = m.indices.map(x => `<div class="mk"><div class="lab">${esc(x.name)}　${esc(x.date)}</div>
      <div class="val num">${nf(x.close, 2)}<span class="${cls(x.chg)}">${pct(x.chg, 2)}</span></div>
      <div class="meta">位階 <span class="pill ${regimeCls(x.regime)}">${esc(x.regime)}</span>　RSI ${nf(x.rsi, 0)}　近 20 日 <span class="${cls(x.ret20)}">${pct(x.ret20)}</span></div>
      <div class="meta">月線 ${nf(x.ma20, 0)}／季線 ${nf(x.ma60, 0)}／年線 ${nf(x.ma240, 0)}</div></div>`).join("");
    const f = m.flows;
    if (f && f.date) {
      const row = (k, v) => `<span>${k}<b class="${cls(v)} num">${v == null ? "—" : (v > 0 ? "+" : "") + nf(v, 1)}</b></span>`;
      h += `<div class="mk"><div class="lab">上市三大法人買賣超（${esc(f.date)}，億元）</div>
        <div class="val num ${cls(f.total)}">${(f.total > 0 ? "+" : "") + nf(f.total, 1)}<span class="muted" style="font-size:13px">合計</span></div>
        <div class="flows">${row("外資", f.foreign)}${row("投信", f.trust)}${row("自營商", f.dealer)}</div></div>`;
    }
    el.innerHTML = h;
  } catch (e) { const el = $("#mk"); if (el) el.innerHTML = `<div class="err">大盤資料暫時無法取得：${esc(e.message)}</div>`; }
}

async function loadWatch() {
  const el = $("#wl"); if (!el) return;
  const list = watch.list();
  if (!list.length) { el.innerHTML = '<div class="empty">還沒有自選股。搜尋一檔股票，在健診頁按「☆ 加入自選」。</div>'; return; }
  try {
    const r = await api(`/api/summary?codes=${encodeURIComponent(list.join(","))}`);
    const byCode = Object.fromEntries(r.rows.map(x => [x.code, x]));
    const rows = list.map(c => byCode[c]).filter(Boolean).sort((a, b) => b.score - a.score);
    const hs = holds.all();
    el.innerHTML = `<div class="tbl-wrap"><table class="tbl"><thead><tr><th>股票</th><th>現價</th><th>漲跌</th><th>健診分數</th><th class="l">評等／建議</th>
      <th class="hide-m">近 60 日</th><th class="hide-m">停損</th><th class="hide-m">我的損益</th><th></th></tr></thead><tbody>
      ${rows.map(s => { const h = hs[s.code]; const pl = h && h.cost ? s.price / h.cost - 1 : null;
        return `<tr class="click" data-code="${esc(s.code)}"><td class="stk"><b>${esc(s.name)}</b><span>${esc(s.code)} · ${boardName(s)}</span></td>
        <td class="num">${px(s.price)}</td><td class="num ${cls(s.chg)}">${pct(s.chg, 2)}</td><td>${sbar(s.score)}</td>
        <td class="l">${ratingPill(s.rating)} ${esc(s.action)}</td><td class="hide-m">${spark(s.spark)}</td>
        <td class="hide-m num">${px(s.stop)}</td><td class="hide-m num ${cls(pl)}">${pl == null ? '<span class="muted">—</span>' : pct(pl)}</td>
        <td><button class="x" data-rm="${esc(s.code)}" title="從自選移除" aria-label="移除">×</button></td></tr>`; }).join("")}
      </tbody></table></div>
      ${r.errors.length ? `<p class="small dn">無法分析：${r.errors.map(e => esc(`${e.code}（${e.error}）`)).join("；")}</p>` : ""}`;
    $$("tr[data-code]", el).forEach(tr => tr.onclick = e => { if (!e.target.closest("[data-rm]")) location.hash = `#/stock/${tr.dataset.code}`; });
    $$("[data-rm]", el).forEach(b => b.onclick = () => { watch.remove(b.dataset.rm); loadWatch(); });
  } catch (e) { el.innerHTML = `<div class="err">${esc(e.message)}</div>`; }
}

function pickCard(s) {
  const sc = s.scores || {};
  const ms = [["趨勢", sc.trend], ["動能", sc.momentum], ["基本", sc.fundamental], ["籌碼", sc.chips]]
    .map(([k, v]) => `<span>${k}<b>${v == null ? "—" : Math.round(v)}</b></span>`).join("");
  return `<div class="pick" data-code="${esc(s.code)}" tabindex="0">
    <div class="row"><h4>${esc(s.name)}<small>${esc(s.code)}</small></h4>${ratingPill(s.rating)}</div>
    <div class="row"><div><div class="num" style="font-size:20px;font-weight:700">${px(s.price)} <span class="${cls(s.chg)}" style="font-size:14px">${pct(s.chg, 2)}</span></div>
      <div style="font-weight:700">${esc(s.action)}</div></div>${gauge(s.score, 96)}</div>
    ${spark(s.spark, 248, 36)}
    <div class="mini-scores">${ms}</div>
    ${s.note ? `<div class="why">入選原因：${esc(s.note)}</div>` : ""}
  </div>`;
}
function bindPicks() {
  $$(".pick").forEach(p => { const go = () => location.hash = `#/stock/${p.dataset.code}`; p.onclick = go; p.onkeydown = e => { if (e.key === "Enter") go(); }; });
}

// ---------------------------------------------------------------- 今日選股
async function viewScan(app) {
  document.title = "今日選股｜股票健診";
  app.innerHTML = `<h1 style="font-size:26px;margin:8px 0 6px">今日選股</h1>
  <p class="muted" style="margin:0 0 6px">每天從全部上市櫃股票中篩出：<b>外資＋投信近 5 日多數天買超</b>、<b>月營收年增 10% 以上</b>、<b>本益比 0~35 倍</b>、<b>成交金額 1 億元以上</b>，再逐檔做完整健診，依分數排序。</p>
  <p class="small muted" style="margin:0 0 16px">入選只代表「法人和基本面同時有動靜」，不代表一定會漲。請點進去看停損和風險報酬比再決定。</p>
  <div id="scanBody">${loading("正在掃描全市場並逐檔健診，第一次約需 30 秒…")}</div>`;
  try {
    const r = await api("/api/scan");
    const el = $("#scanBody"); if (!el) return;
    el.innerHTML = `<p class="small muted">法人資料日期 ${esc(r.date || "—")}　·　產生於 ${esc(r.generated)}</p>
      <div class="cards">${r.rows.map(pickCard).join("") || '<div class="empty">今日沒有符合條件的股票</div>'}</div>`;
    bindPicks();
  } catch (e) { const el = $("#scanBody"); if (el) el.innerHTML = `<div class="err">${esc(e.message)}</div>`; }
}

// ---------------------------------------------------------------- 個股健診
const PILLARS = [
  ["trend", "趨勢", "股價是不是在往上走？看均線位置與方向、中期漲幅"],
  ["momentum", "動能", "最近的上漲力道強不強？看 RSI、KD、MACD、相對大盤強弱"],
  ["volume", "量價", "有沒有資金在買？看成交量是否配合股價"],
  ["fundamental", "基本面", "公司賺不賺錢、貴不貴？看營收成長、本益比、殖利率、EPS"],
  ["chips", "籌碼", "大戶在買還是賣？看外資、投信動向與融資變化"],
  ["risk", "風險控管", "現在追進去危不危險？看乖離、波動度、大盤位階（不計入總分）"],
];
const WEIGHTS = { trend: 30, momentum: 20, volume: 10, fundamental: 22, chips: 18, risk: 0 };

async function viewStock(app, code) {
  document.title = `${code} 健診｜股票健診`;
  app.innerHTML = loading(`正在幫 ${code} 做健診：下載股價、營收、法人資料…`);
  let a;
  try { a = await api(`/api/stock/${encodeURIComponent(code)}`); }
  catch (e) {
    app.innerHTML = `<div class="card"><h3>找不到或無法分析 ${esc(code)}</h3><div class="err">${esc(e.message)}</div>
      <p class="muted">台股請輸入 4~6 碼代號（例如 2330、00878），美股請輸入英文代號（例如 AAPL）。</p><a href="#/">← 回首頁</a></div>`;
    return;
  }
  if (!location.hash.includes(encodeURIComponent(code)) && !location.hash.includes(code)) return; // 使用者已離開
  document.title = `${a.name}（${a.code}）健診 ${Math.round(a.score)} 分｜股票健診`;
  const starOn = watch.has(a.code);
  app.innerHTML = `
  <div class="card">
    <div class="s-head">
      <div class="s-name"><h1>${esc(a.name)}<small>${esc(a.code)} · ${boardName(a)}${a.revenue && a.revenue.industry ? " · " + esc(a.revenue.industry) : ""}</small></h1>
        <div class="s-price num">${px(a.price)}<span class="${cls(a.chg)}">${pct(a.chg, 2)}</span><em>資料日 ${esc(a.date)}${a.currency && a.currency !== "TWD" ? " · " + esc(a.currency) : ""}</em></div>
        <div style="margin-top:10px"><button class="star ${starOn ? "on" : ""}" id="star">${starOn ? "★ 已加入自選" : "☆ 加入自選"}</button></div></div>
      <div class="s-verdict">${gauge(a.score)}<div>${ratingPill(a.rating)}<div class="act">${esc(a.action)}</div>
        <div class="small muted">技術面 ${Math.round(a.tech_score)} 分</div></div></div>
    </div>
    <div class="summary">${summaryHtml(a)}</div>
  </div>
  <h2 class="sec-title">新手五問 <span class="muted">不懂指標也看得懂的結論</span></h2>
  <div class="qs" id="qs"></div>
  <h2 class="sec-title">走勢圖 <span class="muted">紅漲綠跌 · 已還原權息 · 虛線為停損／目標</span></h2>
  <div class="card">${chartShell()}</div>
  <div class="grid2">
    <div class="card"><h3>健診分數怎麼來的 <span class="muted">點開看每一條加減分</span></h3>${pillarsHtml(a)}</div>
    <div class="stack">
      <div class="card"><h3>交易計畫</h3>${planHtml(a)}${calcHtml()}</div>
      <div class="card"><h3>我有這檔股票</h3>${holdHtml(a)}</div>
    </div>
  </div>
  <h2 class="sec-title">關鍵指標 <span class="muted">每個數字下方都有白話解釋</span></h2>
  <div class="card">${indHtml(a)}</div>
  ${a.tw && !a.etf ? `<div class="grid2"><div class="card"><h3>基本面</h3>${fundHtml(a)}</div><div class="card"><h3>籌碼面</h3>${chipsHtml(a)}</div></div>` : ""}
  <h2 class="sec-title">這套模型在這檔股票上準不準？</h2>
  <div class="card">${btHtml(a)}</div>`;

  $("#star").onclick = () => { const on = watch.toggle(a.code); const b = $("#star"); b.classList.toggle("on", on); b.textContent = on ? "★ 已加入自選" : "☆ 加入自選"; };
  bindCalc(a);
  bindHold(a);
  drawCharts(a);
}

function summaryHtml(a) {
  return a.summary.map((s, i) => {
    if (i === 0) return `<div class="lead">${esc(s)}</div>`;
    if (s.startsWith("主要利多：")) return `<div class="pos"><b class="up">利多</b>${esc(s.slice(5))}</div>`;
    if (s.startsWith("主要利空：")) return `<div class="neg"><b class="dn">利空</b>${esc(s.slice(5))}</div>`;
    return `<div class="cav"><b class="neu">注意</b>${esc(s)}</div>`;
  }).join("");
}

function sizing(a) {
  const st = settings(), p = a.plan, risk = a.price - p.stop;
  const byRisk = risk > 0 ? st.capital * st.risk / 100 / risk : 0;
  const byCap = st.capital * st.maxpos / 100 / a.price;
  const shares = Math.max(0, Math.floor(Math.min(byRisk, byCap)));
  return { st, shares, value: shares * a.price, loss: shares * risk, limited: byRisk <= byCap ? "單筆風險" : "單一持股上限" };
}
const sharesText = (a, n) => !a.tw ? `${n.toLocaleString()} 股` : n >= 1000 ? `${Math.floor(n / 1000)} 張${n % 1000 ? ` ${n % 1000} 股` : ""}` : `${n} 股（零股）`;

function drawQs(a) {
  const p = a.plan, price = a.price, z = sizing(a);
  const bear = a.rating === "偏空";
  const inEntry = price >= p.entry[0] && price <= p.entry[1] * 1.003;
  const where = inEntry ? "現價就在進場區內，可以考慮分批。"
    : price > p.entry[1] ? `現價比進場區高 ${pct(price / p.entry[1] - 1)}，等拉回比較划算。`
    : "現價已跌破進場區下緣，先等止跌再說。";
  const can = { "偏多": "多數訊號偏多。", "中性": "多空訊號混雜，先觀察。", "偏空": "整體偏弱，不建議進場。" }[a.rating] || "";
  const rr = p.rr;
  const qs = [
    ["1", "現在能買嗎？", esc(a.action), can + (a.caveats.length ? " " + esc(a.caveats[0]) + "。" : "")],
    ["2", "在哪裡買？", `${px(p.entry[0])} ~ ${px(p.entry[1])}`, (bear ? "（目前不建議進場，僅供參考）" : "") + where],
    ["3", "買錯了怎麼辦？", `<span class="dn">${px(p.stop)}</span> 停損`, `比現價 ${pct(p.stop / price - 1)}。收盤跌破就出場，不要凹單。`],
    ["4", "目標在哪？", `<span class="up">${px(p.target1)}</span>`, `第一目標 ${pct(p.target1 / price - 1)}，第二目標 ${px(p.target2)}。風險報酬比 ${rr ? nf(rr, 1) : "—"}：每冒 1 元風險，預期可賺 ${rr ? nf(rr, 1) : "—"} 元${rr && rr < 1.5 ? "，不太划算" : ""}。`],
    ["5", "要買多少？", z.shares ? sharesText(a, z.shares) : "—", `依你設定的資金 ${nf(z.st.capital / 10000, 0)} 萬、單筆最多虧 ${z.st.risk}%：約 ${nf(z.value, 0)} 元，碰到停損約虧 ${nf(z.loss, 0)} 元。`],
  ];
  $("#qs").innerHTML = qs.map(([n, t, v, d]) => `<div class="q"><div class="qt"><span class="ic">${n}</span>${t}</div><div class="qa num">${v}</div><div class="qd">${d}</div></div>`).join("");
}

function pillarsHtml(a) {
  return PILLARS.map(([k, name, desc]) => {
    const s = a.scores[k], notes = (a.notes[k] || []).slice().sort((x, y) => y[0] - x[0]);
    const w = WEIGHTS[k] ? `權重 ${WEIGHTS[k]}%` : "不計分";
    if (s == null) return `<details class="pl"><summary><span class="pn">${name}</span><span class="bar"></span><span class="ps muted">—</span><span class="pw">不適用</span><span class="chev">▶</span></summary>
      <p class="pdesc">${a.etf ? "ETF 不計算基本面與籌碼。" : "美股暫不提供這項資料。"}</p></details>`;
    return `<details class="pl"><summary><span class="pn">${name}</span><span class="bar"><u style="width:${s}%;background:${sColor(s)}"></u></span>
      <span class="ps" style="color:${sColor(s)}">${Math.round(s)}</span><span class="pw">${w}</span><span class="chev">▶</span></summary>
      <p class="pdesc">${desc}</p>
      <ul class="notes">${notes.map(([p, t]) => `<li class="${p > 0 ? "pos" : "neg"}"><b>${p > 0 ? "+" : ""}${Math.round(p)}</b><span>${esc(t)}</span></li>`).join("") || "<li><span class=\"muted\">沒有明顯訊號（維持 50 分中性）</span></li>"}</ul></details>`;
  }).join("") + `<p class="small muted" style="margin:10px 0 0">每個面向從 50 分（中性）開始，依規則加減分。總分＝各面向依權重平均；ETF、美股缺少的面向會把權重分給其他面向。</p>`;
}

function planHtml(a) {
  const p = a.plan;
  const lv = arr => arr.length ? arr.map(([v, k]) => `${px(v)}<span class="muted small">（${esc(k)}）</span>`).join("、") : "—";
  return `<table class="kv num">
    <tr><th>理想進場區</th><td>${px(p.entry[0])} ～ ${px(p.entry[1])}</td></tr>
    <tr><th>停損</th><td class="dn">${px(p.stop)}（${pct(p.stop / a.price - 1)}）</td></tr>
    <tr><th>目標 1 / 2</th><td class="up">${px(p.target1)}（${pct(p.target1 / a.price - 1)}） / ${px(p.target2)}（${pct(p.target2 / a.price - 1)}）</td></tr>
    <tr><th>風險報酬比</th><td>${p.rr ? nf(p.rr, 1) : "—"} <span class="muted small">（1.5 以上才划算）</span></td></tr>
    <tr><th>移動停利</th><td>${px(p.chandelier)} <span class="muted small">（近 22 日高點 − 3 倍日波動；持股跌破就出場）</span></td></tr>
    <tr><th>支撐</th><td>${lv(p.supports)}</td></tr>
    <tr><th>壓力</th><td>${lv(p.resistances)}</td></tr>
    <tr><th>日均波動</th><td>${px(p.atr)}（${pct(p.atr / a.price, 1, false)}）</td></tr>
  </table>`;
}

function calcHtml() {
  const st = settings();
  return `<div class="calc"><b>部位計算機</b> <span class="small muted">（存在你的瀏覽器）</span>
    <div class="fields">
      <label>總資金（元）<input type="number" id="cCap" value="${st.capital}" min="0" step="10000"></label>
      <label>單筆最多虧（%）<input type="number" id="cRisk" value="${st.risk}" min="0.1" max="10" step="0.1"></label>
      <label>單檔上限（%）<input type="number" id="cMax" value="${st.maxpos}" min="1" max="100" step="1"></label>
    </div><div class="res" id="cRes"></div></div>`;
}
function bindCalc(a) {
  const upd = () => {
    const v = id => parseFloat($(id).value);
    const cur = settings();
    store.set("set", { capital: v("#cCap") > 0 ? v("#cCap") : cur.capital, risk: v("#cRisk") > 0 ? v("#cRisk") : cur.risk, maxpos: v("#cMax") > 0 ? v("#cMax") : cur.maxpos });
    const z = sizing(a);
    $("#cRes").innerHTML = z.shares
      ? `建議買 <b>${sharesText(a, z.shares)}</b>，約 <b>${nf(z.value, 0)}</b> 元；碰到停損約虧 <b class="dn">${nf(z.loss, 0)}</b> 元。<span class="small muted">（受限於${z.limited}）</span>`
      : "資金不足以買進 1 股。";
    drawQs(a);
  };
  ["#cCap", "#cRisk", "#cMax"].forEach(id => $(id).addEventListener("input", upd));
  upd();
}

function holdHtml(a) {
  const h = holds.get(a.code) || {};
  return `<div class="calc" style="margin-top:0"><div class="small muted">輸入你的成本，幫你判斷該續抱還是出場（只存在你的瀏覽器）。</div>
    <div class="fields">
      <label>買進成本（每股）<input type="number" id="hCost" value="${h.cost ?? ""}" min="0" step="any" placeholder="例如 ${px(a.price * 0.9).replace(/,/g, "")}"></label>
      <label>持有股數<input type="number" id="hSh" value="${h.shares ?? ""}" min="0" step="1" placeholder="例如 1000"></label>
    </div>
    <div style="display:flex;gap:8px"><button class="btn" id="hSave">儲存</button><button class="btn ghost" id="hClear">清除</button></div>
    <div id="hRes"></div></div>`;
}
function holdAdvice(a, h) {
  const p = a.plan, i = a.ind, price = a.price;
  const res = p.resistances.length ? p.resistances[0][0] : p.target1;
  if (p.chandelier && price < p.chandelier) return `已跌破移動停利 ${px(p.chandelier)}，建議出場或至少減碼一半。`;
  if (i.ma60 && price < i.ma60 && a.scores.trend < 40) return `跌破季線且趨勢轉弱，建議減碼；剩下的部位停損設 ${px(p.stop)}。`;
  if (a.score >= 60) return `續抱。移動停利設 ${px(p.chandelier)}，跌破再出場；不用預設賣點，讓獲利繼續跑。`;
  if (a.score >= 45) return `續抱觀察，停損設 ${px(p.stop)}；反彈到壓力 ${px(res)} 附近可以考慮先賣一部分。`;
  return `評分偏空，建議減碼${res ? `，或等反彈到 ${px(res)} 附近再賣` : ""}。`;
}
function bindHold(a) {
  const draw = () => {
    const h = holds.get(a.code);
    const el = $("#hRes");
    if (!h || !h.cost) { el.innerHTML = ""; return; }
    const pl = a.price / h.cost - 1, amt = h.shares ? (a.price - h.cost) * h.shares : null;
    el.innerHTML = `<div class="advice">目前報酬 <b class="${cls(pl)}">${pct(pl)}</b>${amt != null ? `（<span class="${cls(amt)}">${amt > 0 ? "+" : ""}${nf(amt, 0)}</span> 元）` : ""}<br><b>建議：</b>${esc(holdAdvice(a, h))}</div>`;
  };
  $("#hSave").onclick = () => { const c = parseFloat($("#hCost").value), s = parseFloat($("#hSh").value); holds.set(a.code, c > 0 ? { cost: c, shares: s > 0 ? s : null } : null); draw(); };
  $("#hClear").onclick = () => { holds.set(a.code, null); $("#hCost").value = ""; $("#hSh").value = ""; draw(); };
  draw();
}

function indHtml(a) {
  const i = a.ind;
  const z = (v, lo, hi, a1, a2, a3) => v == null ? "" : v > hi ? a1 : v < lo ? a3 : a2;
  const items = [
    ["RSI（14）", nf(i.rsi, 0), "0~100 的漲跌力道。" + z(i.rsi, 30, 70, "目前 >70，偏熱。", "目前在 30~70 正常區。", "目前 <30，超跌。")],
    ["KD", `${nf(i.k, 0)} / ${nf(i.d, 0)}`, "短線強弱。K 在 D 之上偏多；>80 過熱、<20 超賣。" + (i.k != null ? (i.k > i.d ? "目前偏多。" : "目前偏空。") : "")],
    ["MACD 柱", `<span class="${cls(i.macd_hist)}">${i.macd_hist == null ? "—" : (i.macd_hist > 0 ? "+" : "") + nf(i.macd_hist, 2)}</span>`, "正值＝多方動能，負值＝空方動能。"],
    ["ADX", nf(i.adx, 0), ">25 代表有明確趨勢（不分漲跌），<20 是盤整。"],
    ["月線乖離", `<span class="${cls(i.bias20)}">${pct(i.bias20)}</span>`, "股價離 20 日均線多遠。超過 +10% 容易回檔。"],
    ["年化波動", pct(i.vol60, 0, false), "一年大約上下震盪的幅度。越高越刺激，部位要越小。"],
    ["距 52 週高點", pct(i.dd252), "離一年內最高價還差多少。"],
    ["近 20 日", `<span class="${cls(i.ret20)}">${pct(i.ret20)}</span>`, "最近一個月的漲跌幅。"],
    ["近 1 年", `<span class="${cls(i.ret240)}">${pct(i.ret240)}</span>`, "最近一年的漲跌幅（還原權息）。"],
    ["中期動能", `<span class="${cls(i.mom12_1)}">${pct(i.mom12_1, 0)}</span>`, "過去 12 個月扣掉最近 1 個月的漲幅。是驗證過最有效的因子之一。"],
    ["相對大盤", `<span class="${cls(i.rs60)}">${pct(i.rs60)}</span>`, "近 60 日比大盤多漲（或少漲）多少。"],
    ["均線", `${px(i.ma20)} / ${px(i.ma60)}`, `月線 / 季線。年線 ${px(i.ma240)}。股價在均線之上代表該期間買的人多數賺錢。`],
  ];
  return `<div class="inds">${items.map(([l, v, e]) => `<div class="ind"><div class="il">${l}</div><div class="iv num">${v}</div><div class="ie">${e}</div></div>`).join("")}</div>`;
}

function fundHtml(a) {
  const v = a.valuation || {}, r = a.revenue || {}, e = a.eps || {};
  const pe = v.pe;
  const items = [
    ["本益比", pe ? nf(pe, 1) + " 倍" : "虧損／—", "股價是一年獲利的幾倍。台股一般 10~20 倍算合理，越低越便宜。"],
    ["殖利率", v.yield != null ? nf(v.yield, 2) + "%" : "—", "現在買進，每年大約能領到的股利比例。"],
    ["股價淨值比", v.pb ? nf(v.pb, 2) : "—", "股價是每股淨資產的幾倍。低於 1 代表比帳面價值便宜。"],
    [`營收年增（${esc(r.period || "")}）`, `<span class="${cls(r.yoy)}">${pp(r.yoy)}</span>`, "這個月營收比去年同月成長多少。"],
    ["營收月增", `<span class="${cls(r.mom)}">${pp(r.mom)}</span>`, "比上個月成長多少（會受季節性影響）。"],
    ["今年累計營收", `<span class="${cls(r.cum_yoy)}">${pp(r.cum_yoy)}</span>`, "今年到目前為止，比去年同期成長多少。"],
    [`${e.year || ""} 前 ${e.quarter || ""} 季 EPS`, e.eps_ytd != null ? nf(e.eps_ytd, 2) + " 元" : "—", "每股賺多少錢（今年累計）。"],
  ];
  return `<div class="inds">${items.map(([l, x, d]) => `<div class="ind"><div class="il">${l}</div><div class="iv num">${x}</div><div class="ie">${d}</div></div>`).join("")}</div>
    ${r.note ? `<p class="small muted" style="margin:10px 0 0">公司說明營收變動原因：${esc(r.note)}</p>` : ""}`;
}

function chipsHtml(a) {
  const inst = a.inst || [], m = a.margin || {};
  if (!inst.length) return '<p class="muted">暫無法人資料。</p>';
  const mx = Math.max(1, ...inst.map(d => Math.abs(d.total)));
  const bars = inst.map(d => { const h = Math.abs(d.total) / mx * 50;
    return `<div class="fb" title="${esc(d.date)} 三大法人 ${nf(d.total, 0)} 張"><div class="mid"></div>
      <div style="height:50%;display:flex;align-items:flex-end">${d.total > 0 ? `<u style="height:${h * 2}%;background:var(--up)"></u>` : ""}</div>
      <div style="height:50%;display:flex;align-items:flex-start">${d.total < 0 ? `<u style="height:${h * 2}%;background:var(--dn)"></u>` : ""}</div></div>`; }).join("");
  const tot = k => inst.reduce((s, d) => s + d[k], 0);
  const td = v => `<td class="num ${cls(v)}">${nf(v, 0)}</td>`;
  let mtxt = "";
  if (m.margin != null) {
    const ch = m.margin_past ? m.margin / m.margin_past - 1 : null;
    mtxt = `<p style="margin:12px 0 0;font-size:14px"><b>融資餘額</b> ${nf(m.margin, 0)} 張${ch != null ? `（兩週 <span class="${cls(ch)}">${pct(ch)}</span>）` : ""}　<b>融券餘額</b> ${nf(m.short, 0)} 張
      <br><span class="small muted">融資是散戶借錢買股。股價跌、融資卻增加，代表散戶在接刀，通常不是好現象。</span></p>`;
  }
  return `<div class="small muted">近 ${inst.length} 日三大法人合計買賣超（張）：紅色是買超、綠色是賣超</div>
    <div class="flowbars">${bars}</div><div class="fb-lab">${inst.map(d => `<span>${esc(d.date.slice(5).replace("-", "/"))}</span>`).join("")}</div>
    <div class="tbl-wrap" style="margin-top:10px"><table class="tbl"><thead><tr><th>日期</th><th>外資</th><th>投信</th><th>自營商</th><th>合計</th></tr></thead><tbody>
    ${inst.slice().reverse().map(d => `<tr><td>${esc(d.date.slice(5).replace("-", "/"))}</td>${td(d.foreign)}${td(d.trust)}${td(d.dealer)}${td(d.total)}</tr>`).join("")}
    <tr class="sum"><td>合計</td>${td(tot("foreign"))}${td(tot("trust"))}${td(tot("dealer"))}${td(tot("total"))}</tr></tbody></table></div>${mtxt}`;
}

function btHtml(a) {
  const bt = a.backtest;
  if (!bt) return `<p>${esc(a.bt_verdict)}</p>`;
  const row = (lab, s) => !s.n ? `<tr><td>${lab}</td><td colspan="3" class="muted">樣本不足</td></tr>`
    : `<tr><td>${lab}</td><td class="num">${s.n}</td><td class="num ${cls(s.mean)}">${pct(s.mean)}</td><td class="num">${pct(s.win, 0, false)}</td></tr>`;
  return `<p style="margin-top:0">${esc(a.bt_verdict)}</p>
    <p class="small muted">做法：用這檔股票 ${esc(bt.start.slice(0, 7))} ~ ${esc(bt.end.slice(0, 7))} 的每一天重算技術分，看之後 ${bt.horizon} 個交易日「比大盤多賺多少」。IC 是分數高低和之後表現的相關程度，越大越準，負的代表反指標。</p>
    <div class="tbl-wrap"><table class="tbl"><thead><tr><th>情境</th><th>天數</th><th>之後 ${bt.horizon} 日比大盤多賺</th><th>贏大盤機率</th></tr></thead><tbody>
    ${row("所有日子（基準）", bt.base)}${row(`技術分 ≥ ${bt.hi}（偏多時）`, bt.bull)}${row(`技術分 ≤ ${bt.lo}（偏空時）`, bt.bear)}${row(`和今天分數（${Math.round(a.tech_score)}）差不多時`, bt.near)}
    </tbody></table></div>`;
}

// ---------------------------------------------------------------- 圖表
function chartShell() {
  return `<div class="chart-tools">
    <div class="seg" id="rng"><button data-n="63">3 個月</button><button data-n="126" class="on">6 個月</button><button data-n="250">1 年</button><button data-n="9999">2 年</button></div>
    <div class="toggles" id="tog">
      <label><input type="checkbox" data-s="ma20" checked><i style="background:var(--ma20)"></i>月線</label>
      <label><input type="checkbox" data-s="ma60" checked><i style="background:var(--ma60)"></i>季線</label>
      <label><input type="checkbox" data-s="ma240"><i style="background:var(--ma240)"></i>年線</label>
      <label><input type="checkbox" data-s="bb"><i style="background:var(--muted)"></i>布林通道</label>
      <label><input type="checkbox" data-s="plan" checked><i style="background:var(--dn)"></i>停損／目標</label>
    </div></div>
    <div class="ohlc" id="ohlc"></div>
    <div id="mainChart"></div>
    <div class="sub-head"><div class="seg" id="subTabs"><button data-t="tech" class="on">技術分</button><button data-t="rsi">RSI</button><button data-t="kd">KD</button><button data-t="macd">MACD</button></div>
      <span class="hint" id="subHint"></span></div>
    <div id="subChart"></div>`;
}

function drawCharts(a) {
  if (!window.LightweightCharts) { $("#mainChart").innerHTML = '<p class="muted">圖表元件載入失敗（可能是網路阻擋 unpkg.com）。</p>'; return; }
  const LC = window.LightweightCharts, d = a.chart, n = d.t.length;
  const css = getComputedStyle(document.documentElement), col = v => css.getPropertyValue(v).trim();
  const base = {
    autoSize: true,
    layout: { background: { type: "solid", color: col("--card") }, textColor: col("--muted"), fontFamily: "Noto Sans TC, Microsoft JhengHei, sans-serif" },
    grid: { vertLines: { color: col("--line") }, horzLines: { color: col("--line") } },
    rightPriceScale: { borderColor: col("--line") },
    timeScale: { borderColor: col("--line"), rightOffset: 3 },
    crosshair: { mode: LC.CrosshairMode.Normal },
    localization: { locale: "zh-TW", dateFormat: "yyyy/MM/dd" },
  };
  const main = LC.createChart($("#mainChart"), base);
  const sub = LC.createChart($("#subChart"), Object.assign({}, base, { timeScale: Object.assign({}, base.timeScale, { visible: false }) }));
  charts.push(main, sub);
  const up = col("--up"), dn = col("--dn");
  const candle = main.addCandlestickSeries({ upColor: up, downColor: dn, borderUpColor: up, borderDownColor: dn, wickUpColor: up, wickDownColor: dn, priceLineVisible: false });
  candle.setData(d.t.map((t, i) => d.o[i] == null ? { time: t } : { time: t, open: d.o[i], high: d.h[i], low: d.l[i], close: d.c[i] }));
  candle.priceScale().applyOptions({ scaleMargins: { top: 0.06, bottom: 0.24 } });
  const vol = main.addHistogramSeries({ priceScaleId: "vol", priceFormat: { type: "volume" }, priceLineVisible: false, lastValueVisible: false });
  main.priceScale("vol").applyOptions({ scaleMargins: { top: 0.8, bottom: 0 } });
  vol.setData(d.t.map((t, i) => ({ time: t, value: d.v[i] || 0, color: (d.c[i] >= (d.o[i] ?? d.c[i]) ? up : dn) + "66" })));
  const line = (arr, color, w = 1.5, extra = {}) => {
    const s = main.addLineSeries(Object.assign({ color, lineWidth: w, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false }, extra));
    s.setData(d.t.map((t, i) => arr[i] == null ? { time: t } : { time: t, value: arr[i] })); return s;
  };
  const S = {
    ma20: [line(d.ma20, col("--ma20"))], ma60: [line(d.ma60, col("--ma60"))], ma240: [line(d.ma240, col("--ma240"), 1.5, { visible: false })],
    bb: [line(d.bbu, col("--muted"), 1, { lineStyle: 2, visible: false }), line(d.bbl, col("--muted"), 1, { lineStyle: 2, visible: false })],
  };
  const p = a.plan, pl = [];
  const addPL = (price, color, title, style = 2) => price && pl.push(candle.createPriceLine({ price, color, lineWidth: 1, lineStyle: style, axisLabelVisible: true, title }));
  const drawPlan = on => { pl.splice(0).forEach(x => candle.removePriceLine(x)); if (!on) return;
    addPL(p.stop, dn, "停損"); addPL(p.target1, up, "目標1"); addPL(p.target2, up, "目標2", 3);
    addPL(p.entry[0], col("--accent"), "進場下緣", 1); addPL(p.entry[1], col("--accent"), "進場上緣", 1); };
  drawPlan(true);

  $$("#tog input").forEach(cb => cb.onchange = () => {
    if (cb.dataset.s === "plan") return drawPlan(cb.checked);
    S[cb.dataset.s].forEach(s => s.applyOptions({ visible: cb.checked }));
  });

  // 十字線資訊列
  const ohlc = $("#ohlc");
  const showBar = i => {
    if (i == null || i < 0 || i >= n || d.c[i] == null) return;
    const chg = i > 0 && d.c[i - 1] ? d.c[i] / d.c[i - 1] - 1 : null;
    ohlc.innerHTML = `${esc(d.t[i])}　開<b>${px(d.o[i])}</b>高<b>${px(d.h[i])}</b>低<b>${px(d.l[i])}</b>收<b class="${cls(chg)}">${px(d.c[i])}（${pct(chg, 2)}）</b>
      量<b>${a.tw ? nf((d.v[i] || 0) / 1000, 0) + " 張" : nf(d.v[i], 0)}</b><span style="color:var(--ma20)">月線</span><b>${px(d.ma20[i])}</b><span style="color:var(--ma60)">季線</span><b>${px(d.ma60[i])}</b>`;
  };
  const idxOf = t => d.t.indexOf(typeof t === "string" ? t : `${t.year}-${String(t.month).padStart(2, "0")}-${String(t.day).padStart(2, "0")}`);
  main.subscribeCrosshairMove(e => showBar(e && e.time ? idxOf(e.time) : n - 1));
  showBar(n - 1);

  // 副圖
  let subSeries = [];
  const subLine = (arr, color, w = 1.5) => { const s = sub.addLineSeries({ color, lineWidth: w, priceLineVisible: false, lastValueVisible: true, crosshairMarkerVisible: false });
    s.setData(d.t.map((t, i) => arr[i] == null ? { time: t } : { time: t, value: arr[i] })); subSeries.push(s); return s; };
  const guide = (s, v, title) => s.createPriceLine({ price: v, color: col("--muted"), lineWidth: 1, lineStyle: 2, axisLabelVisible: false, title });
  const hints = { tech: "技術分 ≥65 偏多、≤40 偏空（趨勢＋動能＋量價）", rsi: ">70 過熱、<30 超賣", kd: "K（實線）在 D（虛線）之上偏多", macd: "柱狀體翻紅＝多方動能增強" };
  const drawSub = t => {
    subSeries.forEach(s => sub.removeSeries(s)); subSeries = [];
    $("#subHint").textContent = hints[t];
    if (t === "tech") { const s = subLine(d.tech.slice(-n), col("--accent"), 2); guide(s, 65, "65"); guide(s, 40, "40"); }
    if (t === "rsi") { const s = subLine(d.rsi, col("--ma60")); guide(s, 70, "70"); guide(s, 30, "30"); }
    if (t === "kd") { const s = subLine(d.k, col("--ma20")); subLine(d.d, col("--ma60")).applyOptions({ lineStyle: 2 }); guide(s, 80, "80"); guide(s, 20, "20"); }
    if (t === "macd") {
      const h = sub.addHistogramSeries({ priceLineVisible: false, lastValueVisible: false });
      h.setData(d.t.map((tt, i) => d.hist[i] == null ? { time: tt } : { time: tt, value: d.hist[i], color: d.hist[i] >= 0 ? up : dn }));
      subSeries.push(h); subLine(d.dif, col("--ma20"), 1.2); subLine(d.dea, col("--ma60"), 1.2);
    }
    const r = main.timeScale().getVisibleLogicalRange(); if (r) sub.timeScale().setVisibleLogicalRange(r);
  };
  $$("#subTabs button").forEach(b => b.onclick = () => { $$("#subTabs button").forEach(x => x.classList.toggle("on", x === b)); drawSub(b.dataset.t); });
  drawSub("tech");

  // 兩張圖同步捲動縮放
  let syncing = false;
  const sync = (from, to) => from.timeScale().subscribeVisibleLogicalRangeChange(r => { if (!r || syncing) return; syncing = true; to.timeScale().setVisibleLogicalRange(r); syncing = false; });
  sync(main, sub); sync(sub, main);
  const setRange = k => main.timeScale().setVisibleLogicalRange({ from: Math.max(0, n - k), to: n + 2 });
  $$("#rng button").forEach(b => b.onclick = () => { $$("#rng button").forEach(x => x.classList.toggle("on", x === b)); setRange(+b.dataset.n); });
  setRange(126);
}

// ---------------------------------------------------------------- 說明頁
async function viewAbout(app) {
  document.title = "怎麼看懂｜股票健診";
  app.innerHTML = `<h1 style="font-size:26px;margin:8px 0 16px">怎麼看懂健診報告</h1>
  <div class="card prose"><h3>健診分數是什麼？</h3>
    <p>每檔股票從 <b>5 個面向</b>各打 0~100 分（50 分是中性），再依權重平均成 <b>健診分數</b>：</p>
    <div class="tbl-wrap"><table class="tbl"><thead><tr><th>面向</th><th class="l">在問什麼</th><th>權重</th></tr></thead><tbody>
    ${PILLARS.map(([k, n, d]) => `<tr><td><b>${n}</b></td><td class="l" style="white-space:normal">${d}</td><td>${WEIGHTS[k] ? WEIGHTS[k] + "%" : "不計分"}</td></tr>`).join("")}
    </tbody></table></div>
    <p>每一條加減分規則都會寫在報告裡（點開「健診分數怎麼來的」），所以你知道分數為什麼是這樣，而不是黑盒子。</p></div>
  <div class="card"><h3>分數對照</h3><div class="tbl-wrap"><table class="tbl"><thead><tr><th>健診分數</th><th class="l">評等</th><th class="l">建議</th></tr></thead><tbody>
    <tr><td>72 以上</td><td class="l">${ratingPill("偏多")}</td><td class="l">買進</td></tr>
    <tr><td>60 ~ 72</td><td class="l">${ratingPill("偏多")}</td><td class="l">逢回分批布局</td></tr>
    <tr><td>45 ~ 60</td><td class="l">${ratingPill("中性")}</td><td class="l">觀望</td></tr>
    <tr><td>35 ~ 45</td><td class="l">${ratingPill("偏空")}</td><td class="l">減碼／暫不進場</td></tr>
    <tr><td>35 以下</td><td class="l">${ratingPill("偏空")}</td><td class="l">賣出／避開</td></tr></tbody></table></div>
    <p class="small muted">另外兩條保護規則：趨勢分數低於 35 時，即使總分高也只給「觀望」（不逆勢摸底）；風險分數低於 35（太熱）時，買進建議會改成「不追高，等回檔」。</p></div>
  <div class="card" id="mc"><h3>這套模型可信嗎？</h3>${loading("載入驗證結果…")}</div>
  <div class="card"><h3>交易計畫怎麼算</h3><div class="prose"><ul>
    <li><b>停損</b>：最近的支撐（均線、近期低點、布林下軌）再往下半個日均波動，並控制在現價下方 1.5~3 倍日均波動之間。</li>
    <li><b>目標</b>：最近的壓力價；如果太近，就用 2 倍、3 倍風險當目標。</li>
    <li><b>要買多少</b>：讓「碰到停損時的虧損」剛好等於總資金的 1%（可自己調整），同時單檔不超過總資金 20%。這是專業交易者最常用的資金控管方法：<b>先決定最多虧多少，再決定買多少</b>。</li>
    <li><b>移動停利</b>：近 22 日最高價減 3 倍日均波動。持股跌破就出場，用來保護已經賺到的獲利。</li></ul></div></div>
  <div class="card"><h3>名詞小辭典</h3><div class="gloss">${[
    ["月線 / 季線 / 年線", "過去 20 / 60 / 240 個交易日的平均股價。股價在均線之上，代表那段期間買進的人多數是賺錢的。"],
    ["多頭排列", "短天期均線在長天期均線之上（例如月線 > 季線），代表趨勢向上。"],
    ["RSI", "0~100，衡量最近上漲力道占漲跌總力道的比例。>70 偏熱，<30 超跌。"],
    ["KD", "看收盤價落在近 9 日高低區間的哪裡。K 向上穿過 D 叫黃金交叉。"],
    ["MACD", "快慢兩條平均線的差距。柱狀體由負轉正代表多方動能增強。"],
    ["ADX", "趨勢強度，不分漲跌。>25 有明確趨勢，<20 是盤整。"],
    ["乖離率", "股價離均線多遠。漲太快乖離過大時，常會拉回修正。"],
    ["OBV", "上漲日加成交量、下跌日減成交量的累計值，用來看資金流向。"],
    ["ATR（日均波動）", "平均每天的高低震幅。停損和部位大小都會根據它調整。"],
    ["三大法人", "外資、投信、自營商。投信連續買超常被視為「認養」訊號。"],
    ["融資 / 融券", "融資是借錢買股（通常是散戶），融券是借股票來賣（看空）。"],
    ["本益比 / PEG", "股價 ÷ 每股盈餘。PEG＝本益比 ÷ 成長率，小於 1 代表成長能支撐高本益比。"],
    ["風險報酬比", "（目標 − 現價）÷（現價 − 停損）。1.5 以上才值得做。"],
    ["IC", "分數排序和之後報酬排序的相關係數。0.05 以上在實務上已經是有用的訊號。"],
  ].map(([k, v]) => `<div><b>${k}</b>${v}</div>`).join("")}</div></div>
  <div class="card prose"><h3>資料來源與更新時間</h3><ul>
    <li>股價：Yahoo Finance，5 年日線，已還原權息（除權息不會被誤判成大跌）。</li>
    <li>本益比、殖利率、月營收、EPS、三大法人、融資融券：臺灣證券交易所、證券櫃檯買賣中心官方 OpenAPI。</li>
    <li>法人資料約每天 16:00 公布，融資融券約 21:00 公布；網站每 20 分鐘自動更新一次。盤中查詢時最新的 K 棒是即時價。</li>
    <li>美股只有技術面分析（基本面與籌碼資料需要付費 API）。</li></ul>
    <p class="small muted">限制：基本面與籌碼沒有可取得的每日歷史，這兩項的權重是依經驗設定、沒有經過回測；驗證樣本是目前仍在交易的權值股，有存活者偏差。本網站僅供研究參考，不構成投資建議。</p></div>`;
  try {
    const m = await api("/api/model");
    const el = $("#mc"); if (!el) return;
    const h = m.results["60"] || m.results[Object.keys(m.results).pop()], h20 = m.results["20"];
    const q = h.quintiles, mx = Math.max(...q.map(Math.abs));
    el.innerHTML = `<h3>這套模型可信嗎？ <span class="muted">用 ${m.stocks} 檔台股、${esc(m.start)} ~ ${esc(m.end)} 的資料驗證</span></h3>
      <p style="margin-top:0">每天把所有股票依技術分高低分成 5 組，看之後 60 個交易日各組比平均多賺多少：</p>
      <div class="qbars">${q.map(v => `<div class="qb"><b class="${cls(v)}">${pct(v)}</b><u class="${v < 0 ? "neg" : ""}" style="height:${Math.abs(v) / mx * 75}%;background:${v >= 0 ? "var(--up)" : "var(--dn)"}"></u></div>`).join("")}</div>
      <div class="qlabs"><span>分數最低 20%</span><span>第 2 組</span><span>第 3 組</span><span>第 4 組</span><span>分數最高 20%</span></div>
      <p>分數越高，之後表現越好，而且是一階一階遞增，代表分數確實有區分能力。技術總分的 IC：未來 20 日 <b>${(h20.tech.ic > 0 ? "+" : "") + nf(h20.tech.ic, 3)}</b>、60 日 <b>${(h.tech.ic > 0 ? "+" : "") + nf(h.tech.ic, 3)}</b>（0.05 以上在實務上就是有用的訊號）。</p>
      <p class="small muted">我們也發現「風險控管」分數（扣分過熱、高波動、大盤跌破季線）的預測力是<b>負的</b>（60 日 IC ${nf(h.risk.ic, 3)}）：被扣分的股票事後反而表現較好。所以風險分數不計入總分，只用來提醒追高與決定部位大小。這是實際檢驗後的調整，不是憑感覺。</p>`;
  } catch (e) { const el = $("#mc"); if (el) el.innerHTML = `<h3>這套模型可信嗎？</h3><p class="muted">驗證資料暫時無法載入。</p>`; }
}

// ---------------------------------------------------------------- 啟動
bindSearch($(".top-search"));
api("/api/defaults").then(d => { DEFAULTS = d; }).catch(() => { /* 用內建預設 */ }).finally(route);
pollWarm();
