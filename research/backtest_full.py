"""完整模型回測（無存活者偏差、時點正確）。結果寫入 model_card.json 的 "full" 區塊。

每月底取樣，用「當時就拿得到」的資料重算健診分數：
- 技術面：只用當天以前的還原股價
- 基本面：只用已公布的月營收（月底時最新的是上個月）、當天的本益比/殖利率/淨值比；EPS 規則因無法時點正確取得而略過
- 籌碼面：當天以前 10 個交易日的三大法人、融資餘額變化
股票池：當天有交易、上市滿一年、近 20 日平均成交金額 ≥ 2000 萬元的一般股票（包含後來下市的）。
下市股票：之後的報酬以最後交易價計算（下市當下的實際損益）。

用法：python research/backtest_full.py
"""
from __future__ import annotations

import datetime as dt
import json
import os
import sys
import warnings

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
warnings.filterwarnings("ignore")

from research.dataset import load  # noqa: E402
from stock_analyzer import scoring  # noqa: E402
from stock_analyzer.data import yahoo_history  # noqa: E402
from stock_analyzer.indicators import add_indicators  # noqa: E402

CARD = os.path.join(ROOT, "model_card.json")
MIN_VALUE = 2e7
HORIZONS = (20, 60)
PILLARS = ["trend", "momentum", "volume", "risk", "tech", "fundamental", "chips", "composite", "industry_mom", "big_holder"]
WEIGHT_SETS = {
    "目前權重（基本面 65%）": scoring.WEIGHTS,
    "原本權重（技術 60%）": {"trend": .30, "momentum": .20, "volume": .10, "fundamental": .22, "chips": .18, "risk": 0},
    "只看技術面": {"trend": .30 / .6, "momentum": .20 / .6, "volume": .10 / .6, "fundamental": 0, "chips": 0, "risk": 0},
    "只看基本面": {"trend": 0, "momentum": 0, "volume": 0, "fundamental": 1, "chips": 0, "risk": 0},
    "五面向平均": {"trend": .2, "momentum": .2, "volume": .2, "fundamental": .2, "chips": .2, "risk": 0},
}


def log(m):
    print(f"[{dt.datetime.now():%H:%M:%S}] {m}", flush=True)


def spearman(a: pd.Series, b: pd.Series):
    m = a.notna() & b.notna()
    if m.sum() < 30:
        return np.nan
    return float(np.corrcoef(a[m].rank(), b[m].rank())[0, 1])


def tstat(s: pd.Series, step: int):
    x = s.dropna().iloc[::step]
    return float(x.mean() / x.std() * np.sqrt(len(x))) if len(x) > 2 and x.std() > 0 else None


def comp(row, w):
    used = {k: row[k] for k in ("trend", "momentum", "volume", "fundamental", "chips", "risk") if w.get(k) and pd.notna(row[k])}
    tot = sum(w[k] for k in used)
    return sum(row[k] * w[k] for k in used) / tot if tot else np.nan


def main():
    ds = load()
    twii, _ = yahoo_history("^TWII", "10y")
    rev = ds["revenue"]
    industry = {}
    for (c, y, m), v in sorted(rev.items(), key=lambda kv: (kv[0][1], kv[0][2])):
        industry[c] = v.get("industry")

    # 合併上市+上櫃面板
    # 上櫃轉上市的股票兩邊都有：以上市資料為主，轉板前的期間用上櫃資料補
    adj = {k: ds["listed"]["adj"][k].combine_first(ds["otc"]["adj"][k]) for k in ("open", "high", "low", "close", "volume", "value")}
    raw_close = ds["listed"]["raw"]["close"].combine_first(ds["otc"]["raw"]["close"])
    board = {c: "listed" for c in ds["listed"]["names"]}
    board.update({c: "otc" for c in ds["otc"]["names"]})
    names = {**ds["listed"]["names"], **ds["otc"]["names"]}
    samples = [d for d in ds["listed"]["samples"] if pd.Timestamp(d) in adj["close"].index]
    last_day = adj["close"].index[-1]
    alive = set(adj["close"].columns[adj["close"].iloc[-5:].notna().any()])
    log(f"{len(names)} 檔、{len(samples)} 個取樣月、目前仍交易 {len(alive)} 檔")

    close_ff = adj["close"].ffill()
    pos = {d: i for i, d in enumerate(adj["close"].index)}
    fwd = {h: close_ff.shift(-h) / close_ff - 1 for h in HORIZONS}
    idx_close = twii["close"].reindex(adj["close"].index).ffill()
    idx_fwd = {h: idx_close.shift(-h) / idx_close - 1 for h in HORIZONS}
    sample_ts = [pd.Timestamp(d) for d in samples]

    # ---- 技術面：逐檔算完整歷史，再取取樣日
    recs = []
    cols = list(adj["close"].columns)
    for k, code in enumerate(cols, 1):
        c = adj["close"][code]
        if c.notna().sum() < 300:
            continue
        df = pd.DataFrame({x: adj[x][code] for x in ("open", "high", "low", "close", "volume")}).dropna(subset=["close"])
        df[["open", "high", "low"]] = df[["open", "high", "low"]].apply(lambda s: s.fillna(df["close"]))
        df["volume"] = df["volume"].fillna(0)
        df["raw_close"] = raw_close[code].reindex(df.index)
        try:
            d = add_indicators(df, twii["close"])
            _, ser, tech = scoring.technical(d)
        except Exception:  # noqa: BLE001
            continue
        val20 = adj["value"][code].rolling(20, min_periods=10).mean()
        for ts in sample_ts:
            if ts not in d.index or pos[ts] < 260:
                continue
            i_local = d.index.get_loc(ts)
            if i_local < 250 or not (val20.get(ts, 0) >= MIN_VALUE):
                continue
            row = d.iloc[i_local]
            recs.append({"date": ts, "code": code, "trend": ser["trend"].iloc[i_local], "momentum": ser["momentum"].iloc[i_local],
                         "volume": ser["volume"].iloc[i_local], "risk": ser["risk"].iloc[i_local], "tech": tech.iloc[i_local],
                         "price": row.raw_close, "vol_ma20": row.vol_ma20, "mom12_1": row.mom12_1, "value20": val20.get(ts),
                         "ret10": d["close"].iloc[i_local] / d["close"].iloc[i_local - 10] - 1,
                         **{f"fwd{h}": fwd[h].at[ts, code] for h in HORIZONS},
                         **{f"ifwd{h}": idx_fwd[h].at[ts] for h in HORIZONS}})
        if k % 300 == 0:
            log(f"技術面 {k}/{len(cols)}")
    R = pd.DataFrame(recs)
    log(f"樣本 {len(R)} 筆")

    # ---- 基本面、籌碼面（時點正確）
    fund, chips = [], []
    for r in R.itertuples():
        d = r.date.date()
        y, m = (d.year, d.month - 1) if d.month > 1 else (d.year - 1, 12)
        rv = rev.get((r.code, y, m)) or {}
        rv = dict(rv, period=f"{y}/{m:02d}") if rv else {}
        snap = ds["listed"]["snaps"].get(d) or {}
        if r.code not in (snap.get("inst") or {}) and r.code not in (snap.get("pe") or {}):
            snap = ds["otc"]["snaps"].get(d) or {}  # 當時在上櫃
        val = (snap.get("pe") or {}).get(r.code) or {}
        fp = scoring.score_fundamental(r.price, val, rv, {}) if (val or rv) else None
        fund.append(fp.result() if fp else np.nan)
        inst_rows = (snap.get("inst") or {}).get(r.code) or []
        if inst_rows:
            inst = pd.DataFrame(inst_rows, columns=["date", "foreign", "trust", "dealer", "total"]).sort_values("date")
            mn, mp = (snap.get("margin") or {}).get(r.code), (snap.get("margin_past") or {}).get(r.code)
            mg = {"margin": mn[0], "short": mn[1], "margin_past": mp[0]} if (mn and mp and mn[0] is not None) else {}
            cp = scoring.score_chips(inst, mg, (r.vol_ma20 or 0) / 1000, r.ret10)
            chips.append(cp.result() if cp else np.nan)
        else:
            chips.append(np.nan)
    R["fundamental"], R["chips"] = fund, chips
    R["industry"] = R["code"].map(industry)
    # 產業動能：同產業其他股票 12-1 月動能中位數
    R["industry_mom"] = np.nan
    for (dte, ind), g in R.groupby(["date", "industry"]):
        if len(g) < 4:
            continue
        s = g["mom12_1"]
        tot, n = s.sum(), s.notna().sum()
        R.loc[g.index, "industry_mom"] = (tot - s.fillna(0)) / (n - s.notna().astype(int)).replace(0, np.nan)
    for name, w in WEIGHT_SETS.items():
        R[f"comp::{name}"] = R.apply(lambda row: comp(row, w), axis=1)
    R["composite"] = R["comp::目前權重（基本面 65%）"]
    R.to_pickle(os.path.join(ROOT, "research", "data", "samples.pkl"))

    # ---- 千張大戶（集保一年樣本）
    big = big_holder_study(close_ff)
    if big is not None:
        R = R  # 只在報告中呈現，樣本不同

    card = json.load(open(CARD, encoding="utf-8")) if os.path.exists(CARD) else {}
    full = {"generated": dt.date.today().isoformat(),
            "universe": {"stocks": int(R["code"].nunique()), "delisted": int(len(set(R["code"]) - alive)),
                         "samples": len(samples), "start": str(samples[0]), "end": str(samples[-1]), "min_value": MIN_VALUE,
                         "rows": int(len(R))}}
    ic_out = {}
    for h in HORIZONS:
        f = f"fwd{h}"
        step = 1 if h <= 21 else 3
        res = {}
        for col in ["trend", "momentum", "volume", "risk", "tech", "fundamental", "chips", "composite", "industry_mom"]:
            s = R.groupby("date").apply(lambda g: spearman(g[col], g[f]))
            res[col] = {"ic": float(s.mean()), "t": tstat(s, step), "hit": float((s.dropna() > 0).mean())}
        ic_out[str(h)] = res
    full["ic"] = ic_out

    def quint(col, f):
        d = R.dropna(subset=[col, f]).copy()
        d["q"] = d.groupby("date")[col].transform(lambda x: pd.qcut(x.rank(method="first"), 5, labels=False))
        d["rel"] = d[f] - d.groupby("date")[f].transform("mean")
        return [float(v) for v in d.groupby("q")["rel"].mean().sort_index()]
    full["quintiles"] = {str(h): quint("composite", f"fwd{h}") for h in HORIZONS}
    full["quintiles_tech"] = {str(h): quint("tech", f"fwd{h}") for h in HORIZONS}

    # 依建議分組（用目前的評等門檻 + 趨勢保護規則）
    def action(row):
        s = row["composite"]
        if pd.isna(s):
            return None
        r, act, _ = scoring.rating(s, {"trend": row["trend"], "risk": row["risk"]})
        return act
    R["action"] = R.apply(action, axis=1)
    buckets = []
    for act in ["買進", "逢回分批布局", "不追高，等回檔", "觀望", "減碼／暫不進場", "賣出／避開"]:
        g = R[R["action"] == act]
        if len(g) < 30:
            continue
        b = {"action": act, "n": int(len(g))}
        for h in HORIZONS:
            ex = (g[f"fwd{h}"] - g[f"ifwd{h}"]).dropna()
            b[f"ex{h}"], b[f"win{h}"] = float(ex.mean()), float((ex > 0).mean())
        buckets.append(b)
    full["ratings"] = buckets

    # 存活者偏差：同樣的技術分，只用「今天還活著」的股票算會差多少
    surv = R[R["code"].isin(alive)]
    def ic_of(df, col, f):
        return float(df.groupby("date").apply(lambda g: spearman(g[col], g[f])).mean())
    def topq(df, col, f):
        d = df.dropna(subset=[col, f]).copy()
        d["q"] = d.groupby("date")[col].transform(lambda x: pd.qcut(x.rank(method="first"), 5, labels=False))
        return float(d[d.q == 4][f].mean())
    full["survivorship"] = {
        "tech_ic_all": ic_of(R, "tech", "fwd60"), "tech_ic_survivors": ic_of(surv, "tech", "fwd60"),
        "top_q_all": topq(R, "composite", "fwd60"), "top_q_survivors": topq(surv, "composite", "fwd60"),
        "mean_all": float(R["fwd60"].mean()), "mean_survivors": float(surv["fwd60"].mean())}

    weights = []
    for name in WEIGHT_SETS:
        col = f"comp::{name}"
        e = {"name": name, "weights": WEIGHT_SETS[name]}
        for h in HORIZONS:
            s = R.groupby("date").apply(lambda g: spearman(g[col], g[f"fwd{h}"]))
            e[f"ic{h}"] = float(s.mean())
            e[f"t{h}"] = tstat(s, 1 if h <= 21 else 3)
        e["spread60"] = (lambda q: q[-1] - q[0])(quint(col, "fwd60"))
        weights.append(e)
    full["weights"] = weights
    if big is not None:
        full["big_holder"] = big
    card["full"] = full
    with open(CARD, "w", encoding="utf-8") as f:
        json.dump(card, f, ensure_ascii=False, indent=2)
    log("已寫入 model_card.json")
    print(json.dumps(full, ensure_ascii=False, indent=1)[:6000])


def big_holder_study(close_ff: pd.DataFrame):
    """千張大戶 4 週變化 vs 之後 4 週（約 20 個交易日）報酬的橫斷面 IC。"""
    path = os.path.join(ROOT, "research", "data", "tdcc_sample.json")
    if not os.path.exists(path):
        return None
    js = json.load(open(path, encoding="utf-8"))
    dates = sorted(js["dates"])  # 每 4 週一期
    ics, rows = [], []
    for i in range(1, len(dates) - 1):
        d0, d1 = dates[i - 1], dates[i]
        t1 = pd.Timestamp(d1)
        if t1 > close_ff.index[-1]:
            continue
        p = close_ff.index.searchsorted(t1)
        if p + 20 >= len(close_ff.index):
            continue
        xs, ys = [], []
        for c, w in js["data"].items():
            if d0 in w and d1 in w and c in close_ff.columns:
                chg = w[d1]["big1000"] - w[d0]["big1000"]
                r = close_ff[c].iloc[p + 20] / close_ff[c].iloc[p] - 1
                if pd.notna(r):
                    xs.append(chg)
                    ys.append(r)
                    rows.append((d1, chg, r))
        if len(xs) >= 30:
            ics.append(spearman(pd.Series(xs), pd.Series(ys)))
    if not ics:
        return None
    s = pd.Series(ics)
    d = pd.DataFrame(rows, columns=["date", "chg", "ret"])
    d["q"] = d.groupby("date")["chg"].transform(lambda x: pd.qcut(x.rank(method="first"), 5, labels=False))
    d["rel"] = d["ret"] - d.groupby("date")["ret"].transform("mean")
    return {"ic": float(s.mean()), "t": float(s.mean() / s.std() * np.sqrt(len(s))) if s.std() > 0 else None,
            "periods": int(len(s)), "stocks": len(js["codes"]), "hit": float((s > 0).mean()),
            "quintiles": [float(v) for v in d.groupby("q")["rel"].mean().sort_index()]}


if __name__ == "__main__":
    main()
