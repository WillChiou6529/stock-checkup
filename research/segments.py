"""分群檢驗：大型股（每月成交金額前 150 名）vs 中小型股，各面向 IC 與候選權重。

目的：只採用「兩群都站得住」的結論，避免權重只適合某一種樣本。
用法：python research/segments.py（需先跑 backtest_full.py 產生 samples.pkl）
"""
from __future__ import annotations

import json
import os
import sys

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

CANDIDATES = {
    "原本（技術 60%）": {"trend": .30, "momentum": .20, "volume": .10, "fundamental": .22, "chips": .18},
    "基本面 40%": {"trend": .25, "momentum": .15, "volume": .05, "fundamental": .40, "chips": .15},
    "基本面 45%、籌碼 10%": {"trend": .25, "momentum": .15, "volume": .05, "fundamental": .45, "chips": .10},
    "基本面 50%、籌碼 5%": {"trend": .25, "momentum": .15, "volume": .05, "fundamental": .50, "chips": .05},
    "只看基本面": {"trend": 0, "momentum": 0, "volume": 0, "fundamental": 1, "chips": 0},
}


def sp(a, b):
    m = a.notna() & b.notna()
    return np.corrcoef(a[m].rank(), b[m].rank())[0, 1] if m.sum() >= 20 else np.nan


def comp(df, w):
    num = sum(df[k].fillna(0) * v for k, v in w.items() if v)
    den = sum(df[k].notna() * v for k, v in w.items() if v)
    return num / den.replace(0, np.nan)


def ic(df, col, h):
    s = df.groupby("date").apply(lambda g: sp(g[col], g[f"fwd{h}"]))
    step = 1 if h <= 21 else 3
    x = s.dropna().iloc[::step]
    t = x.mean() / x.std() * np.sqrt(len(x)) if len(x) > 2 else np.nan
    return float(s.mean()), float(t)


def main():
    R = pd.read_pickle(os.path.join(ROOT, "research", "data", "samples.pkl"))
    R["rank_val"] = R.groupby("date")["value20"].rank(ascending=False)
    seg = {"大型股（成交額前 150）": R[R.rank_val <= 150], "中小型股": R[R.rank_val > 150], "全部": R}
    out = {"pillars": {}, "weights": {}}
    for name, d in seg.items():
        out["pillars"][name] = {}
        for col in ["trend", "momentum", "volume", "risk", "tech", "fundamental", "chips", "industry_mom"]:
            out["pillars"][name][col] = {str(h): ic(d, col, h) for h in (20, 60)}
        out["weights"][name] = {}
        for wn, w in CANDIDATES.items():
            d = d.copy()
            d["c"] = comp(d, w)
            out["weights"][name][wn] = {str(h): ic(d, "c", h) for h in (20, 60)}
    for name in seg:
        print(f"\n=== {name}（{len(seg[name])} 筆）")
        for col, v in out["pillars"][name].items():
            print(f"  {col:12s} IC20 {v['20'][0]:+.3f} (t {v['20'][1]:+.1f})   IC60 {v['60'][0]:+.3f} (t {v['60'][1]:+.1f})")
        print("  -- 權重組合")
        for wn, v in out["weights"][name].items():
            print(f"  {wn:16s} IC20 {v['20'][0]:+.3f} (t {v['20'][1]:+.1f})   IC60 {v['60'][0]:+.3f} (t {v['60'][1]:+.1f})")
    with open(os.path.join(ROOT, "research", "data", "segments.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
