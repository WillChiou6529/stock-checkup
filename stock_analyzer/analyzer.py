"""單一股票分析流程 + 大盤概況。"""
from __future__ import annotations

import re

import pandas as pd

from . import backtest, scoring
from .data import TWMarket, yahoo_history
from .indicators import add_indicators

TW_CODE = re.compile(r"^\d{4,6}[A-Z]?$")


def is_tw(code: str) -> bool:
    return bool(TW_CODE.match(code.upper()))


class Context:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.twii, _ = yahoo_history("^TWII", "5y")
        self._gspc = None
        self.tw = TWMarket(list(self.twii.index))

    @property
    def gspc(self):
        if self._gspc is None:
            self._gspc, _ = yahoo_history("^GSPC", "5y")
        return self._gspc


def market_overview(ctx: Context, include_us: bool):
    out = []
    for name, df in [("加權指數", ctx.twii)] + ([("S&P 500", ctx.gspc)] if include_us else []):
        d = add_indicators(df)
        L, P = d.iloc[-1], d.iloc[-2]
        regime = ("多頭" if L.close > L.ma60 and L.ma20 > L.ma60 else
                  "空頭" if L.close < L.ma60 and L.close < L.ma240 else
                  "整理/轉弱" if L.close < L.ma60 else "整理偏多")
        out.append({
            "name": name, "date": d.index[-1].date(), "close": L.raw_close, "chg": L.raw_close / P.raw_close - 1,
            "ma20": L.ma20, "ma60": L.ma60, "ma240": L.ma240, "rsi": L.rsi, "ret20": L.ret20,
            "regime": regime, "bias60": L.bias60,
        })
    flows = ctx.tw.market_flows()
    return out, flows


def _summary(name, code, price, score, r, act, pillars_notes, caveats, extra):
    lead = f"{name}（{code}）現價 {price:,.2f}，綜合評分 {score:.0f}/100，評等「{r}」，建議：{act}。"
    ranked = sorted(pillars_notes, key=lambda x: x[0])
    pos = [t for p, t in reversed(ranked) if p > 0][:3]
    neg = [t for p, t in ranked if p < 0][:3]
    s = [lead]
    if pos:
        s.append("主要利多：" + "；".join(pos) + "。")
    if neg:
        s.append("主要利空：" + "；".join(neg) + "。")
    s += [c + "。" for c in caveats]
    s += extra
    return s


def analyze(code: str, ctx: Context, note: str | None = None, overrides: dict | None = None,
            chart_days: int = 160) -> dict:
    """overrides 可覆寫 capital / risk_per_trade_pct / max_position_pct / holding（網站用）。"""
    cfg = dict(ctx.cfg)
    cfg.update({k: v for k, v in (overrides or {}).items() if v is not None and k != "holding"})
    code = code.strip().upper()
    tw = is_tw(code)
    board = None
    if tw:
        hit = ctx.tw.resolve(code)
        if not hit:
            raise ValueError(f"{code} 不在上市/上櫃清單中")
        sym, name, board = hit
        idx_df = ctx.twii
    else:
        sym, name, idx_df = code, code, ctx.gspc

    try:
        raw, meta = yahoo_history(sym, "5y")
    except RuntimeError as e:
        if tw:
            raise
        raise ValueError(f"找不到 {code} 的股價資料，請確認代號是否正確") from e
    if len(raw) < 80:
        raise ValueError(f"{code} 歷史資料只有 {len(raw)} 天，不足以分析")
    if not tw:
        name = meta.get("shortName") or meta.get("longName") or code
    df = add_indicators(raw, idx_df["close"])
    L = df.iloc[-1]
    price = float(meta.get("last_price") or L.raw_close)

    pillars, series, tech = scoring.technical(df)
    scores = {k: float(series[k].iloc[-1]) for k in scoring.TECH_KEYS}
    notes = {k: pillars[k].notes for k in scoring.TECH_KEYS}

    etf = tw and code.startswith("0")
    fund = val = rev = eps = margin = None
    inst = pd.DataFrame()
    if tw and not etf:
        val = ctx.tw.valuation(code, board)
        rev = ctx.tw.revenue(code, board)
        eps = ctx.tw.eps(code, board)
        fp = scoring.score_fundamental(price, val, rev, eps)
        n = int(cfg.get("chip_lookback_days", 10))
        inst = ctx.tw.institutional(code, board, n)
        margin = ctx.tw.margin(code, board, n)
        avg_lots = float(L.vol_ma20) / 1000 if pd.notna(L.vol_ma20) else 0
        ret10 = float(df["close"].iloc[-1] / df["close"].iloc[-11] - 1) if len(df) > 11 else None
        cp = scoring.score_chips(inst, margin, avg_lots, ret10)
        for k, p in (("fundamental", fp), ("chips", cp)):
            scores[k] = p.result() if p else None
            notes[k] = p.notes if p else []
    else:
        scores["fundamental"] = scores["chips"] = None
        notes["fundamental"] = notes["chips"] = []

    score = scoring.composite(scores)
    r, act, caveats = scoring.rating(score, scores)
    plan = scoring.trade_plan(df, price, tw, float(cfg.get("capital", 1_000_000)),
                              float(cfg.get("risk_per_trade_pct", 1.0)), float(cfg.get("max_position_pct", 20)))
    holding = (overrides or {}).get("holding") or (cfg.get("holdings") or {}).get(code)
    hold = scoring.holder_advice(price, plan, score, scores, holding, df)
    bt = backtest.validate(df, tech, float(tech.iloc[-1]))

    weighted = [(p * scoring.WEIGHTS[k], t) for k, ns in notes.items() for p, t in ns]
    extra = []
    if etf:
        extra.append("ETF 不計基本面與籌碼分數，以技術面與大盤為主。")
    elif not tw:
        extra.append("美股僅提供技術面分析（基本面/籌碼資料源需付費 API）。")
    if plan["rr"] is not None and plan["rr"] < 1.5 and act in ("買進", "逢回分批布局"):
        extra.append(f"以現價進場的風險報酬比僅 {plan['rr']:.1f}，建議等回到進場區再買。")

    return {
        "code": code, "name": name, "symbol": sym, "board": board, "tw": tw, "etf": etf, "note": note,
        "price": price, "date": df.index[-1].date(), "chg": price / float(df.raw_close.iloc[-2]) - 1,
        "currency": meta.get("currency", ""),
        "score": score, "tech_score": float(tech.iloc[-1]), "scores": scores, "notes": notes,
        "rating": r, "action": act, "caveats": caveats,
        "summary": _summary(name, code, price, score, r, act, weighted, caveats, extra),
        "plan": plan, "holding": hold, "backtest": bt, "bt_verdict": backtest.verdict(bt),
        "valuation": val, "revenue": rev, "eps": eps, "inst": inst, "margin": margin,
        "last": L, "df": df.tail(chart_days), "tech_hist": tech.tail(chart_days),
    }
