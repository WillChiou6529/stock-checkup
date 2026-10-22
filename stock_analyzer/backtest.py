"""用個股自己的歷史驗證「技術面評分」是否有預測力。

衡量的是「相對大盤的超額報酬」（個股 N 日報酬 − 大盤 N 日報酬），排除大盤漲跌的干擾，
回答的問題是：這檔股票技術分高的時候，之後有沒有跑贏大盤？

基本面/籌碼沒有每日歷史，故只驗證技術面分數。樣本為每日滾動，前後 N 日報酬有重疊，
樣本數會高估獨立性；這是「規則在這檔股票上過去是否站得住腳」的參考，不是保證。
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def _stats(x: pd.Series):
    if len(x) == 0:
        return {"n": 0, "mean": None, "median": None, "win": None}
    return {"n": int(len(x)), "mean": float(x.mean()), "median": float(x.median()), "win": float((x > 0).mean())}


def validate(df: pd.DataFrame, tech: pd.Series, today_score: float, horizon: int = 20,
             hi: float = 65, lo: float = 40, warmup: int = 250):
    fwd = df["close"].shift(-horizon) / df["close"] - 1
    if "idx_close" in df:
        fwd = fwd - (df["idx_close"].shift(-horizon) / df["idx_close"] - 1)
    ok = tech.notna() & fwd.notna()
    ok.iloc[:warmup] = False
    if ok.sum() < 120:
        return None
    t, f = tech[ok], fwd[ok]
    # 排序相關係數（Spearman IC），避免依賴 scipy
    ic = float(np.corrcoef(t.rank(), f.rank())[0, 1])
    near = (t - today_score).abs() <= 5
    return {
        "horizon": horizon, "start": t.index[0].date(), "end": t.index[-1].date(),
        "base": _stats(f), "bull": _stats(f[t >= hi]), "bear": _stats(f[t <= lo]),
        "near": _stats(f[near]), "ic": ic, "hi": hi, "lo": lo,
    }


def verdict(bt) -> str:
    """把回測數字翻成一句話。"""
    if not bt:
        return "歷史資料不足，無法驗證。"
    b, bull, bear = bt["base"], bt["bull"], bt["bear"]
    h = bt["horizon"]
    parts = []
    if bull["n"] >= 30:
        parts.append(f"技術分 ≥{bt['hi']:.0f} 之後 {h} 日平均超額報酬 {bull['mean']:+.1%}（跑贏大盤機率 {bull['win']:.0%}）")
    if bear["n"] >= 30:
        parts.append(f"技術分 ≤{bt['lo']:.0f} 之後 {bear['mean']:+.1%}（跑贏機率 {bear['win']:.0%}）")
    if bull["n"] >= 30 and bear["n"] >= 30:
        spread = bull["mean"] - bear["mean"]
        parts.append(f"高低分差距 {spread:+.1%}")
    ic = bt["ic"]
    if ic > 0.08:
        q = "技術訊號在這檔股票上有一定參考價值"
    elif ic > 0.03:
        q = "技術訊號在這檔股票上參考價值偏弱"
    elif ic > -0.03:
        q = "技術訊號在這檔股票上幾乎沒有預測力，請以基本面/籌碼為主"
    else:
        q = "技術訊號在這檔股票上呈反向（強勢後常落後大盤），技術分宜打折看待"
    parts.append(f"{q}（IC={ic:+.2f}，平均超額 {b['mean']:+.1%}）")
    return "；".join(parts) + "。"
