"""建議績效追蹤：每天記錄評分快照（data/signals.csv），事後計算每個建議的實際表現（data/track.json）。

由每日排程（GitHub Actions）執行，網站只讀結果檔，所以 Render 休眠也不會漏記。
"""
from __future__ import annotations

import csv
import datetime as dt
import json
import os

import numpy as np
import pandas as pd

from .data import yahoo_history

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SIGNALS = os.path.join(ROOT, "data", "signals.csv")
TRACK = os.path.join(ROOT, "data", "track.json")
COLS = ["date", "code", "name", "price", "score", "tech", "trend", "momentum", "volume", "fundamental", "chips",
        "risk", "rating", "action", "stop", "target1", "source"]
HORIZONS = (5, 20, 60)
ACTION_ORDER = ["買進", "逢回分批布局", "不追高，等回檔", "觀望", "減碼／暫不進場", "賣出／避開"]


def read_signals() -> list:
    if not os.path.exists(SIGNALS):
        return []
    with open(SIGNALS, encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def save_signals(rows: list):
    os.makedirs(os.path.dirname(SIGNALS), exist_ok=True)
    rows = sorted({(r["date"], r["code"]): r for r in rows}.values(), key=lambda r: (r["date"], r["code"]))
    with open(SIGNALS, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLS, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def row_from(a: dict, date: str, source: str) -> dict:
    s = a["scores"]
    f = lambda v: "" if v is None else f"{v:.1f}"  # noqa: E731
    return {"date": date, "code": a["code"], "name": a["name"], "price": f"{a['price']:.2f}", "score": f(a["score"]),
            "tech": f(a["tech_score"]), "trend": f(s["trend"]), "momentum": f(s["momentum"]), "volume": f(s["volume"]),
            "fundamental": f(s.get("fundamental")), "chips": f(s.get("chips")), "risk": f(s["risk"]),
            "rating": a["rating"], "action": a["action"], "stop": a["plan"]["stop"], "target1": a["plan"]["target1"],
            "source": source}


def evaluate(rows: list, symbols: dict) -> dict:
    """symbols: {code: yahoo_symbol}。計算每筆建議之後的報酬、超額報酬、是否觸及停損/目標。"""
    twii, _ = yahoo_history("^TWII", "2y")
    spx, _ = yahoo_history("^GSPC", "2y")
    cache = {}
    out = []
    for r in rows:
        code, sym = r["code"], symbols.get(r["code"])
        if not sym:
            continue
        if sym not in cache:
            try:
                cache[sym] = yahoo_history(sym, "2y")[0]
            except Exception:  # noqa: BLE001
                cache[sym] = None
        df = cache[sym]
        if df is None:
            continue
        d0 = pd.Timestamp(r["date"])
        after = df[df.index > d0]
        idx = twii if sym.endswith((".TW", ".TWO")) else spx
        p0 = float(r["price"])
        rec = {k: r[k] for k in ("date", "code", "name", "rating", "action", "score")}
        rec.update({"price": p0, "days": int(len(after)), "now": float(df["raw_close"].iloc[-1])})
        rec["ret_now"] = rec["now"] / p0 - 1
        i0 = idx[idx.index <= d0]
        ib = float(i0["close"].iloc[-1]) if len(i0) else None
        rec["excess_now"] = rec["ret_now"] - (float(idx["close"].iloc[-1]) / ib - 1) if ib else None
        for h in HORIZONS:
            if len(after) >= h:
                # 用還原價算報酬（避免除權息被當成下跌）
                base = df["close"][df.index <= d0]
                b = float(base.iloc[-1]) if len(base) else None
                ret = float(after["close"].iloc[h - 1]) / b - 1 if b else None
                ia = idx[idx.index > d0]
                iret = float(ia["close"].iloc[min(h, len(ia)) - 1]) / ib - 1 if (ib and len(ia)) else None
                rec[f"ret{h}"] = ret
                rec[f"ex{h}"] = ret - iret if (ret is not None and iret is not None) else None
        stop, tgt = float(r["stop"] or 0), float(r["target1"] or 0)
        lows, highs = after["low"] * after["raw_close"] / after["close"], after["high"] * after["raw_close"] / after["close"]
        s_hits, t_hits = lows[lows <= stop], highs[highs >= tgt]
        hit_stop = s_hits.index.min() if (stop and not s_hits.empty) else None
        hit_tgt = t_hits.index.min() if (tgt and not t_hits.empty) else None
        rec["hit"] = ("停損" if (hit_stop is not None and (hit_tgt is None or hit_stop <= hit_tgt))
                      else "目標" if hit_tgt is not None else "")
        out.append(rec)
    return out


def summarize(recs: list) -> dict:
    df = pd.DataFrame(recs)
    if df.empty:
        return {"buckets": [], "n": 0}
    buckets = []
    for act in ACTION_ORDER:
        g = df[df["action"] == act]
        if g.empty:
            continue
        b = {"action": act, "n": int(len(g))}
        for h in HORIZONS:
            col = f"ex{h}"
            if col in g and g[col].notna().sum():
                x = g[col].dropna()
                b[f"ex{h}"] = float(x.mean())
                b[f"win{h}"] = float((x > 0).mean())
                b[f"n{h}"] = int(len(x))
        x = g["excess_now"].dropna()
        b["ex_now"] = float(x.mean()) if len(x) else None
        b["stop_rate"] = float((g["hit"] == "停損").mean())
        b["target_rate"] = float((g["hit"] == "目標").mean())
        buckets.append(b)
    return {"buckets": buckets, "n": int(len(df)), "first": df["date"].min(), "last": df["date"].max(),
            "days": int(df["date"].nunique())}


def build_track(symbols: dict) -> dict:
    rows = read_signals()
    recs = evaluate(rows, symbols)
    summary = summarize(recs)
    # 最近的「買進／逢回布局」建議，每檔取最早那次（避免同一檔天天重複）
    picks = {}
    for r in sorted(recs, key=lambda r: r["date"]):
        if r["action"] in ("買進", "逢回分批布局") and r["code"] not in picks:
            picks[r["code"]] = r
    recent = sorted(picks.values(), key=lambda r: r["date"], reverse=True)[:40]
    clean = lambda v: None if isinstance(v, float) and (np.isnan(v) or np.isinf(v)) else v  # noqa: E731
    data = {"generated": dt.datetime.now().strftime("%Y-%m-%d %H:%M"), "summary": summary,
            "picks": [{k: clean(v) for k, v in r.items()} for r in recent]}
    os.makedirs(os.path.dirname(TRACK), exist_ok=True)
    with open(TRACK, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1, default=str)
    return data
