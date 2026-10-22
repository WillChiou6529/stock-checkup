"""產生自足式 HTML 報告（內嵌 SVG 圖表，無外部相依）。台股慣例：紅漲綠跌。"""
from __future__ import annotations

import datetime as dt
import html
import json
import os

import pandas as pd

from .scoring import PILLAR_NAMES, WEIGHTS

E = html.escape


def _px(v, tw=True):
    if v is None or (isinstance(v, float) and v != v):
        return "—"
    if abs(v) >= 1000:
        return f"{v:,.0f}" if tw else f"{v:,.2f}"
    return f"{v:,.2f}"


def _pct(v, d=1, sign=True):
    if v is None or (isinstance(v, float) and v != v):
        return "—"
    return f"{v:+.{d}%}" if sign else f"{v:.{d}%}"


def _cls(v):
    if v is None or v != v or v == 0:
        return ""
    return "up" if v > 0 else "dn"


def _badge(rating):
    return {"偏多": "b-bull", "中性": "b-neu", "偏空": "b-bear"}.get(rating, "b-neu")


def _score_color(s):
    if s is None:
        return "var(--muted)"
    if s >= 60:
        return "var(--up)"
    if s < 45:
        return "var(--dn)"
    return "var(--neu)"


# ------------------------------------------------------------------ SVG 圖
def _chart(a) -> str:
    df = a["df"]
    tech = a["tech_hist"]
    plan = a["plan"]
    n = len(df)
    W, H1, H2, H3, PADL, PADR, GAP = 760, 230, 60, 46, 8, 58, 10
    H = H1 + H2 + H3 + GAP * 2 + 18
    cols = [df.close, df.ma20, df.ma60, df.bb_up, df.bb_lo]
    lo = min(float(c.min()) for c in cols if c.notna().any())
    hi = max(float(c.max()) for c in cols if c.notna().any())
    for v in (plan["stop"], plan["target1"]):
        if v and lo * 0.85 < v < hi * 1.15:
            lo, hi = min(lo, v), max(hi, v)
    pad = (hi - lo) * 0.05 or 1
    lo, hi = lo - pad, hi + pad
    xw = (W - PADL - PADR) / max(n - 1, 1)
    X = lambda i: PADL + i * xw  # noqa: E731
    Y = lambda v: H1 - (v - lo) / (hi - lo) * H1  # noqa: E731

    def path(s):
        pts, out = [], []
        for i, v in enumerate(s):
            if v == v:
                pts.append(f"{X(i):.1f},{Y(v):.1f}")
            elif pts:
                out.append("M" + " L".join(pts))
                pts = []
        if pts:
            out.append("M" + " L".join(pts))
        return " ".join(out)

    g = [f'<svg viewBox="0 0 {W} {H}" class="chart" role="img" aria-label="{E(a["name"])} 走勢圖">']
    # 格線與價格刻度
    for k in range(5):
        v = lo + (hi - lo) * k / 4
        y = Y(v)
        g.append(f'<line x1="{PADL}" x2="{W - PADR}" y1="{y:.1f}" y2="{y:.1f}" class="grid"/>')
        g.append(f'<text x="{W - PADR + 4}" y="{y + 4:.1f}" class="ax">{_px(v, a["tw"])}</text>')
    # 布林通道
    up, dn = df.bb_up.tolist(), df.bb_lo.tolist()
    idx = [i for i in range(n) if up[i] == up[i] and dn[i] == dn[i]]
    if idx:
        poly = [f"{X(i):.1f},{Y(up[i]):.1f}" for i in idx] + [f"{X(i):.1f},{Y(dn[i]):.1f}" for i in reversed(idx)]
        g.append(f'<polygon points="{" ".join(poly)}" class="band"/>')
    # 進場區、停損、目標
    e0, e1 = plan["entry"]
    if lo < e0 < hi:
        g.append(f'<rect x="{PADL}" width="{W - PADL - PADR}" y="{Y(min(e1, hi)):.1f}" '
                 f'height="{max(Y(e0) - Y(min(e1, hi)), 1):.1f}" class="entry"/>')
    for v, cls, lab in ((plan["stop"], "stopl", "停損"), (plan["target1"], "tgtl", "目標1")):
        if v and lo < v < hi:
            g.append(f'<line x1="{PADL}" x2="{W - PADR}" y1="{Y(v):.1f}" y2="{Y(v):.1f}" class="{cls}"/>')
            g.append(f'<text x="{PADL + 4}" y="{Y(v) - 3:.1f}" class="lab {cls}t">{lab} {_px(v, a["tw"])}</text>')
    g.append(f'<path d="{path(df.ma60.tolist())}" class="ma60"/>')
    g.append(f'<path d="{path(df.ma20.tolist())}" class="ma20"/>')
    g.append(f'<path d="{path(df.close.tolist())}" class="px"/>')
    # 成交量
    y0 = H1 + GAP
    vmax = float(df.volume.max()) or 1
    bw = max(xw * 0.7, 1)
    ch = df.close.diff().tolist()
    for i, v in enumerate(df.volume.tolist()):
        h = v / vmax * H2
        c = "vup" if ch[i] == ch[i] and ch[i] > 0 else "vdn"
        g.append(f'<rect x="{X(i) - bw / 2:.1f}" y="{y0 + H2 - h:.1f}" width="{bw:.1f}" height="{h:.1f}" class="{c}"/>')
    g.append(f'<text x="{W - PADR + 4}" y="{y0 + 10}" class="ax">量</text>')
    # 技術分
    y1 = y0 + H2 + GAP
    Yt = lambda v: y1 + H3 - v / 100 * H3  # noqa: E731
    g.append(f'<line x1="{PADL}" x2="{W - PADR}" y1="{Yt(50):.1f}" y2="{Yt(50):.1f}" class="grid"/>')
    tp = [f"{X(i):.1f},{Yt(v):.1f}" for i, v in enumerate(tech.tolist()) if v == v]
    if tp:
        g.append(f'<path d="M{" L".join(tp)}" class="tech"/>')
    g.append(f'<text x="{W - PADR + 4}" y="{y1 + 10}" class="ax">技術分</text>')
    g.append(f'<text x="{W - PADR + 4}" y="{Yt(50) + 4:.1f}" class="ax">50</text>')
    # 月份刻度
    last_m = None
    for i, d in enumerate(df.index):
        if d.month != last_m:
            if last_m is not None:
                g.append(f'<text x="{X(i):.1f}" y="{H - 2}" class="ax" text-anchor="middle">{d.month}月</text>')
            last_m = d.month
    g.append("</svg>")
    return "".join(g)


# ------------------------------------------------------------------ 區塊
def _pillars(a) -> str:
    rows = []
    for k in ("trend", "momentum", "volume", "fundamental", "chips", "risk"):
        s = a["scores"].get(k)
        w = WEIGHTS[k]
        wl = f"權重 {w:.0%}" if w else "不計分"
        if s is None:
            rows.append(f'<details class="pl"><summary><span class="pn">{PILLAR_NAMES[k]}</span>'
                        f'<span class="bar"><i style="width:0"></i></span><span class="ps">—</span>'
                        f'<span class="pw">不適用</span></summary></details>')
            continue
        notes = sorted(a["notes"].get(k, []), key=lambda x: -x[0])
        items = "".join(f'<li class="{"pos" if p > 0 else "neg"}"><b>{p:+.0f}</b> {E(t)}</li>' for p, t in notes)
        rows.append(
            f'<details class="pl"><summary><span class="pn">{PILLAR_NAMES[k]}</span>'
            f'<span class="bar"><i style="width:{s:.0f}%;background:{_score_color(s)}"></i></span>'
            f'<span class="ps">{s:.0f}</span><span class="pw">{wl}</span></summary>'
            f'<ul class="notes">{items or "<li>無明顯訊號</li>"}</ul></details>')
    return "".join(rows)


def _plan(a) -> str:
    p, tw = a["plan"], a["tw"]
    unit = ""
    if a["tw"]:
        lots = p["shares"] / 1000
        unit = f"{p['shares']:,} 股（{lots:.0f} 張）" if p["shares"] >= 1000 else f"{p['shares']:,} 股（零股）"
        if p["shares"] >= 1000 and p["shares"] % 1000:
            unit = f"{p['shares']:,} 股（{p['shares'] // 1000} 張 + {p['shares'] % 1000} 股零股）"
    else:
        unit = f"{p['shares']:,} 股"
    sup = "、".join(f"{_px(v, tw)}（{E(k)}）" for v, k in p["supports"]) or "—"
    res = "、".join(f"{_px(v, tw)}（{E(k)}）" for v, k in p["resistances"]) or "—"
    rr = f"{p['rr']:.1f}" if p["rr"] else "—"
    return f"""
<table class="kv">
<tr><th>理想進場區</th><td>{_px(p['entry'][0], tw)} ～ {_px(p['entry'][1], tw)}</td></tr>
<tr><th>停損</th><td class="dn">{_px(p['stop'], tw)}（{_pct(p['stop'] / a['price'] - 1)}）</td></tr>
<tr><th>目標 1 ／ 目標 2</th><td class="up">{_px(p['target1'], tw)}（{_pct(p['target1'] / a['price'] - 1)}）／ {_px(p['target2'], tw)}（{_pct(p['target2'] / a['price'] - 1)}）</td></tr>
<tr><th>風險報酬比</th><td>{rr}</td></tr>
<tr><th>建議部位</th><td>{unit}，約 {p['position_value']:,.0f} 元<span class="muted">（受限於{p['limited_by']}）</span></td></tr>
<tr><th>移動停利</th><td>{_px(p['chandelier'], tw)}<span class="muted">（22 日高點 − 3×ATR）</span></td></tr>
<tr><th>支撐</th><td>{sup}</td></tr>
<tr><th>壓力</th><td>{res}</td></tr>
<tr><th>ATR(14)</th><td>{_px(p['atr'], tw)}（日均波動 {p['atr'] / a['price']:.1%}）</td></tr>
</table>"""


def _indicators(a) -> str:
    L, tw = a["last"], a["tw"]
    items = [
        ("RSI(14)", f"{L.rsi:.0f}"), ("K / D", f"{L.k:.0f} / {L.d:.0f}"),
        ("MACD 柱", f'<span class="{_cls(L.macd_hist)}">{L.macd_hist:+.2f}</span>'),
        ("ADX", f"{L.adx:.0f}"), ("月線乖離", f'<span class="{_cls(L.bias20)}">{_pct(L.bias20)}</span>'),
        ("年化波動", _pct(L.vol60, 0, False)), ("距 52 週高", _pct(L.dd252)),
        ("20 日漲跌", f'<span class="{_cls(L.ret20)}">{_pct(L.ret20)}</span>'),
        ("60 日漲跌", f'<span class="{_cls(L.ret60)}">{_pct(L.ret60)}</span>'),
        ("12-1 月動能", f'<span class="{_cls(L.mom12_1)}">{_pct(L.mom12_1, 0)}</span>'),
        ("相對大盤 60 日", f'<span class="{_cls(L.get("rs60"))}">{_pct(L.get("rs60"))}</span>'),
        ("MA20 / MA60", f"{_px(L.ma20, tw)} / {_px(L.ma60, tw)}"),
        ("MA240 年線", _px(L.ma240, tw)),
    ]
    return '<div class="grid4">' + "".join(f'<div class="ind"><span>{k}</span><b>{v}</b></div>' for k, v in items) + "</div>"


def _fund_chips(a) -> str:
    if not a["tw"] or a["etf"]:
        return ""
    out = []
    v, r, e = a["valuation"] or {}, a["revenue"] or {}, a["eps"] or {}
    fund = [
        ("本益比", f"{v['pe']:.1f}" if v.get("pe") else "虧損/—"), ("殖利率", f"{v['yield']:.2f}%" if v.get("yield") is not None else "—"),
        ("股價淨值比", f"{v['pb']:.2f}" if v.get("pb") else "—"),
        (f"{r.get('period', '')} 營收年增", f'<span class="{_cls(r.get("yoy"))}">{r["yoy"]:+.1f}%</span>' if r.get("yoy") is not None else "—"),
        ("營收月增", f'<span class="{_cls(r.get("mom"))}">{r["mom"]:+.1f}%</span>' if r.get("mom") is not None else "—"),
        ("累計營收年增", f'<span class="{_cls(r.get("cum_yoy"))}">{r["cum_yoy"]:+.1f}%</span>' if r.get("cum_yoy") is not None else "—"),
        (f"{e.get('year', '')} 前 {e.get('quarter', '')} 季 EPS", f"{e['eps_ytd']:.2f}" if e.get("eps_ytd") is not None else "—"),
        ("產業", E(r.get("industry") or "—")),
    ]
    out.append('<h4>基本面</h4><div class="grid4">' + "".join(f'<div class="ind"><span>{k}</span><b>{x}</b></div>' for k, x in fund) + "</div>")
    if r.get("note"):
        out.append(f'<p class="muted small">營收備註：{E(r["note"])}</p>')

    inst = a["inst"]
    if inst is not None and len(inst):
        def c(v):
            return f'<td class="{_cls(v)}">{v:,.0f}</td>'
        rows = "".join(f"<tr><td>{d.date:%m/%d}</td>{c(d.foreign)}{c(d.trust)}{c(d.dealer)}{c(d.total)}</tr>"
                       for d in inst.itertuples())
        tot = inst[["foreign", "trust", "dealer", "total"]].sum()
        rows += f'<tr class="sum"><td>合計</td>{c(tot.foreign)}{c(tot.trust)}{c(tot.dealer)}{c(tot.total)}</tr>'
        out.append('<h4>三大法人買賣超（張）</h4><div class="scroll"><table class="tbl num">'
                   '<tr><th>日期</th><th>外資</th><th>投信</th><th>自營商</th><th>合計</th></tr>'
                   f"{rows}</table></div>")
    m = a["margin"] or {}
    if m:
        txt = f"融資餘額 {m['margin']:,.0f} 張、融券餘額 {m['short']:,.0f} 張（{m['date']:%m/%d}）"
        if m.get("margin_past"):
            ch = m["margin"] / m["margin_past"] - 1 if m["margin_past"] else 0
            txt += f"；較 {m['past_date']:%m/%d} 融資 <span class=\"{_cls(ch)}\">{_pct(ch)}</span>"
        out.append(f"<h4>信用交易</h4><p>{txt}</p>")
    return "".join(out)


def _backtest(a) -> str:
    bt = a["backtest"]
    if not bt:
        return f"<p>{E(a['bt_verdict'])}</p>"
    def row(lab, s):
        if not s["n"]:
            return f"<tr><td>{lab}</td><td colspan=3>樣本不足</td></tr>"
        return (f"<tr><td>{lab}</td><td>{s['n']}</td><td class=\"{_cls(s['mean'])}\">{_pct(s['mean'])}</td>"
                f"<td>{s['win']:.0%}</td></tr>")
    return (f"<p>{E(a['bt_verdict'])}</p>"
            f'<table class="tbl num"><tr><th>情境（{bt["start"]:%Y/%m}～{bt["end"]:%Y/%m}）</th><th>天數</th>'
            f"<th>之後 {bt['horizon']} 日平均超額報酬</th><th>跑贏大盤機率</th></tr>"
            + row("所有日子", bt["base"]) + row(f"技術分 ≥ {bt['hi']:.0f}", bt["bull"])
            + row(f"技術分 ≤ {bt['lo']:.0f}", bt["bear"])
            + row(f"與今日分數（{a['tech_score']:.0f}）相近 ±5", bt["near"]) + "</table>")


def _stock_card(a) -> str:
    tw = a["tw"]
    hold = ""
    if a["holding"]:
        h = a["holding"]
        pnl = ""
        if h.get("pnl_pct") is not None:
            pnl = f'成本 {_px(h["cost"], tw)}，報酬 <b class="{_cls(h["pnl_pct"])}">{_pct(h["pnl_pct"])}</b>'
            if h.get("pnl") is not None:
                pnl += f'（{h["pnl"]:+,.0f} 元）'
        hold = f'<div class="hold"><b>持股建議</b>　{pnl}<br>{E(h["advice"])}</div>'
    note = f'<div class="scan">掃描入選：{E(a["note"])}</div>' if a.get("note") else ""
    summary = "".join(f"<p>{E(s)}</p>" for s in a["summary"])
    board = {"listed": "上市", "otc": "上櫃"}.get(a["board"], "美股" if not tw else "")
    return f"""
<section class="card" id="s-{E(a['code'])}">
  <div class="head">
    <div><h2>{E(a['name'])} <small>{E(a['code'])} · {board}{' · ETF' if a['etf'] else ''}</small></h2>
      <div class="price">{_px(a['price'], tw)} <span class="{_cls(a['chg'])}">{_pct(a['chg'], 2)}</span>
      <span class="muted small">資料日 {a['date']:%Y/%m/%d}</span></div></div>
    <div class="scorebox"><div class="big" style="color:{_score_color(a['score'])}">{a['score']:.0f}</div>
      <span class="badge {_badge(a['rating'])}">{E(a['rating'])}</span><div class="act">{E(a['action'])}</div></div>
  </div>
  {note}{hold}
  <div class="summary">{summary}</div>
  <div class="cols">
    <div class="col">{_chart(a)}
      <div class="legend"><span class="lg px"></span>收盤（還原）<span class="lg ma20"></span>MA20<span class="lg ma60"></span>MA60
        <span class="lg band"></span>布林通道<span class="lg entry"></span>進場區</div>
      <h4>評分明細 <span class="muted small">（點開看加減分理由）</span></h4>{_pillars(a)}
    </div>
    <div class="col"><h4>交易計畫</h4>{_plan(a)}<h4>關鍵指標</h4>{_indicators(a)}</div>
  </div>
  {_fund_chips(a)}
  <h4>這套模型在這檔股票上準不準？</h4>{_backtest(a)}
</section>"""


def _market(market, flows) -> str:
    cards = []
    for m in market:
        cards.append(f"""<div class="mk"><span class="muted">{E(m['name'])}</span>
<b>{m['close']:,.2f}</b> <span class="{_cls(m['chg'])}">{_pct(m['chg'], 2)}</span>
<div class="small">位階：<b>{E(m['regime'])}</b>　RSI {m['rsi']:.0f}　20 日 {_pct(m['ret20'])}</div>
<div class="small muted">MA20 {m['ma20']:,.0f}／MA60 {m['ma60']:,.0f}／MA240 {m['ma240']:,.0f}</div></div>""")
    if flows:
        f = flows
        cards.append(f"""<div class="mk"><span class="muted">上市三大法人（{f['date']:%m/%d}，億元）</span>
<div>外資 <b class="{_cls(f.get('foreign'))}">{f.get('foreign', 0):+,.1f}</b>　投信 <b class="{_cls(f.get('trust'))}">{f.get('trust', 0):+,.1f}</b></div>
<div>自營 <b class="{_cls(f.get('dealer'))}">{f.get('dealer', 0):+,.1f}</b>　合計 <b class="{_cls(f.get('total'))}">{f.get('total', 0):+,.1f}</b></div></div>""")
    return '<div class="mkrow">' + "".join(cards) + "</div>"


def _overview(results) -> str:
    rows = []
    for a in results:
        s = a["scores"]
        cell = lambda k: (f'<td style="color:{_score_color(s[k])}">{s[k]:.0f}</td>' if s.get(k) is not None  # noqa: E731
                          else '<td class="muted">—</td>')
        rr = f"{a['plan']['rr']:.1f}" if a["plan"]["rr"] else "—"
        tag = ' <span class="tag">掃描</span>' if a.get("note") else ""
        tag += ' <span class="tag">持股</span>' if a.get("holding") else ""
        delta = ""
        if a.get("prev"):
            dv = a["score"] - a["prev"][1]
            delta = f'<br><span class="small {_cls(dv)}" title="前次 {a["prev"][0]}">{dv:+.0f}</span>' if abs(dv) >= 1 else ""
        rows.append(
            f'<tr><td><a href="#s-{E(a["code"])}">{E(a["code"])}</a></td><td class="nm">{E(a["name"])}{tag}</td>'
            f'<td>{_px(a["price"], a["tw"])}</td><td class="{_cls(a["chg"])}">{_pct(a["chg"], 2)}</td>'
            f'<td><b style="color:{_score_color(a["score"])}">{a["score"]:.0f}</b>{delta}</td>'
            f'<td><span class="badge {_badge(a["rating"])}">{E(a["rating"])}</span></td><td class="nm">{E(a["action"])}</td>'
            + "".join(cell(k) for k in ("trend", "momentum", "volume", "fundamental", "chips", "risk"))
            + f"<td>{rr}</td></tr>")
    return ('<div class="scroll"><table class="tbl ov"><tr><th>代號</th><th>名稱</th><th>現價</th><th>漲跌</th><th>總分</th>'
            '<th>評等</th><th>建議</th><th>趨勢</th><th>動能</th><th>量價</th><th>基本</th><th>籌碼</th><th>風險</th>'
            '<th>R/R</th></tr>' + "".join(rows) + "</table></div>")


def _model_card(path) -> str:
    try:
        with open(path, encoding="utf-8") as f:
            mc = json.load(f)
    except (OSError, ValueError):
        return "<p class=\"muted\">尚未執行 validate_model.py，無模型驗證資料。</p>"
    out = [f"<p>以 {mc['stocks']} 檔台股、{mc['start']} ～ {mc['end']} 的資料做橫斷面檢定（{mc['generated']} 產生）。"
           "IC 為每日「分數排序」與「之後報酬排序」的相關係數平均；0.05 以上在實務上已屬有用的訊號。</p>"]
    for h, r in mc["results"].items():
        q = r["quintiles"]
        out.append(f"<p><b>未來 {h} 日</b>：技術總分 IC {r['tech']['ic']:+.3f}（t={r['tech']['t']:+.1f}，"
                   f"{r['tech']['hit']:.0%} 的交易日為正）；依技術分分五組，相對報酬由低到高為 "
                   + " → ".join(f'<span class="{_cls(x)}">{x:+.1%}</span>' for x in q) + "。"
                   f"風險分數 IC {r['risk']['ic']:+.3f}（為負，故不計入總分）。</p>")
    return "".join(out)


CSS = """
:root{--bg:#f6f7f9;--card:#fff;--fg:#1d2330;--muted:#6b7385;--line:#e3e6ec;--up:#d6303b;--dn:#13955b;--neu:#b7791f;
--px:#1f3b73;--ma20:#e08a1e;--ma60:#7c4dcc;--band:rgba(90,120,200,.10);--entry:rgba(214,48,59,.10);--accent:#2b5bd7}
@media (prefers-color-scheme:dark){:root:not([data-theme=light]){--bg:#11141a;--card:#191d25;--fg:#e6e9ef;--muted:#949cad;
--line:#2a303b;--up:#ff5c66;--dn:#2fc884;--neu:#e2a93b;--px:#9db7ff;--ma20:#f0a54a;--ma60:#b18cff;--band:rgba(130,160,255,.10);
--entry:rgba(255,92,102,.12);--accent:#7aa2ff}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.6 "Microsoft JhengHei","PingFang TC","Noto Sans TC",sans-serif}
.wrap{max-width:1180px;margin:0 auto;padding:20px 16px 60px}h1{font-size:24px;margin:0 0 4px}h2{margin:0;font-size:21px}
h2 small{font-size:13px;color:var(--muted);font-weight:normal}h3{margin:28px 0 10px;font-size:18px}h4{margin:18px 0 8px;font-size:15px}
.muted{color:var(--muted)}.small{font-size:13px}.up{color:var(--up)}.dn{color:var(--dn)}a{color:var(--accent);text-decoration:none}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:18px;margin:16px 0}
.head{display:flex;justify-content:space-between;gap:12px;flex-wrap:wrap}.price{font-size:22px;font-weight:600;margin-top:4px}
.price .small{font-weight:normal;margin-left:8px}.scorebox{text-align:center;min-width:120px}.big{font-size:40px;font-weight:700;line-height:1}
.act{font-weight:600;margin-top:4px}.badge{display:inline-block;padding:1px 10px;border-radius:99px;font-size:13px;font-weight:600}
.b-bull{background:rgba(214,48,59,.14);color:var(--up)}.b-bear{background:rgba(19,149,91,.14);color:var(--dn)}.b-neu{background:rgba(183,121,31,.15);color:var(--neu)}
.summary{margin:12px 0;padding:10px 14px;border-left:3px solid var(--accent);background:var(--bg);border-radius:6px}.summary p{margin:4px 0}
.cols{display:grid;grid-template-columns:1.35fr 1fr;gap:20px}@media (max-width:860px){.cols{grid-template-columns:1fr}}
.chart{width:100%;height:auto;display:block}.chart .grid{stroke:var(--line);stroke-width:1}.chart .ax{fill:var(--muted);font-size:11px}
.chart .px{fill:none;stroke:var(--px);stroke-width:2}.chart .ma20{fill:none;stroke:var(--ma20);stroke-width:1.4}
.chart .ma60{fill:none;stroke:var(--ma60);stroke-width:1.4}.chart .band{fill:var(--band)}.chart .entry{fill:var(--entry)}
.chart .stopl{stroke:var(--dn);stroke-dasharray:5 4;stroke-width:1.2}.chart .tgtl{stroke:var(--up);stroke-dasharray:5 4;stroke-width:1.2}
.chart .lab{font-size:11px}.chart .stoplt{fill:var(--dn)}.chart .tgtlt{fill:var(--up)}.chart .vup{fill:var(--up);opacity:.55}
.chart .vdn{fill:var(--dn);opacity:.55}.chart .tech{fill:none;stroke:var(--accent);stroke-width:1.4}
.legend{font-size:12px;color:var(--muted);margin:4px 0 6px}.lg{display:inline-block;width:16px;height:3px;margin:0 4px 3px 10px;vertical-align:middle}
.lg.px{background:var(--px)}.lg.ma20{background:var(--ma20)}.lg.ma60{background:var(--ma60)}.lg.band{background:var(--band);height:10px}.lg.entry{background:var(--entry);height:10px}
.pl{border-bottom:1px solid var(--line)}.pl summary{display:flex;align-items:center;gap:10px;padding:6px 0;cursor:pointer;list-style:none}
.pl summary::-webkit-details-marker{display:none}.pn{width:64px;font-weight:600}.bar{flex:1;height:8px;background:var(--line);border-radius:9px;overflow:hidden}
.bar i{display:block;height:100%;border-radius:9px}.ps{width:30px;text-align:right;font-weight:700}.pw{width:66px;font-size:12px;color:var(--muted)}
.notes{margin:2px 0 10px;padding-left:18px;font-size:14px}.notes li.pos b{color:var(--up)}.notes li.neg b{color:var(--dn)}
.kv{width:100%;border-collapse:collapse;font-size:14px}.kv th{text-align:left;color:var(--muted);font-weight:normal;padding:5px 8px 5px 0;white-space:nowrap;vertical-align:top}
.kv td{padding:5px 0;border-bottom:1px solid var(--line)}.grid4{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:8px}
.ind{background:var(--bg);border-radius:8px;padding:6px 10px}.ind span{display:block;font-size:12px;color:var(--muted)}.ind b{font-size:15px}
.tbl{border-collapse:collapse;width:100%;font-size:14px}.tbl th,.tbl td{padding:6px 8px;border-bottom:1px solid var(--line);text-align:right;white-space:nowrap}
.tbl th:first-child,.tbl td:first-child{text-align:left}.tbl tr.sum td{font-weight:700}.ov td.nm{text-align:left}.ov th{position:sticky;top:0;background:var(--card)}
.scroll{overflow-x:auto}.tag{font-size:11px;background:var(--bg);border:1px solid var(--line);border-radius:6px;padding:0 5px;color:var(--muted)}
.mkrow{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:12px}.mk{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:12px 14px}
.mk b{font-size:18px}.hold{margin:10px 0;padding:10px 14px;border-radius:8px;background:var(--entry)}.scan{margin:10px 0;font-size:14px;color:var(--muted)}
.warn{font-size:13px;color:var(--muted);border-top:1px solid var(--line);margin-top:30px;padding-top:12px}
"""


def render(results, errors, market, flows, warnings, out_dir, model_card_path) -> str:
    now = dt.datetime.now()
    body = [f"""<div class="wrap"><h1>每日股票分析報告</h1>
<div class="muted">{now:%Y/%m/%d %H:%M} 產生 · 共 {len(results)} 檔 · 價格來源 Yahoo Finance（還原權息）· 基本面/籌碼來源 證交所、櫃買中心 OpenAPI</div>
<h3>大盤概況</h3>{_market(market, flows)}
<h3>總覽（依綜合評分排序）</h3>{_overview(results)}
<p class="small muted">總分 = 基本面 65% + 趨勢 15% + 風險控管 10% + 籌碼 10%（依 2022~2026 全市場回測決定；不適用的面向權重按比例分給其他面向）。
≥72 買進、60~72 逢回布局、45~60 觀望、35~45 減碼、&lt;35 賣出／避開。動能與量價僅供參考、不計分。</p>"""]
    if errors:
        body.append('<p class="small dn">無法分析：' + "；".join(E(f"{c}（{m}）") for c, m in errors) + "</p>")
    body += [_stock_card(a) for a in results]
    body.append(f'<h3>模型可信度</h3><div class="card">{_model_card(model_card_path)}</div>')
    if warnings:
        body.append('<p class="small muted">資料警告：' + "；".join(E(w) for w in warnings[:10]) + "</p>")
    body.append("""<div class="warn"><b>免責聲明</b>：本報告由程式依公開資料與固定規則自動產生，僅供研究參考，不構成任何投資建議。
回測與統計檢定反映的是過去，不保證未來；樣本有存活者偏差。基本面與籌碼權重為經驗設定、未經回測。
盤中執行時最新一根 K 棒為即時價，收盤後數字可能變動。投資前請自行判斷並控管風險。</div></div>""")

    page = (f'<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width,initial-scale=1"><title>股票分析 {now:%Y-%m-%d}</title>'
            f"<style>{CSS}</style></head><body>{''.join(body)}</body></html>")
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, f"{now:%Y-%m-%d}.html")
    with open(path, "w", encoding="utf-8") as f:
        f.write(page)
    return path
