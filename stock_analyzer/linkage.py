"""國際連動（美股前一晚 -> 台股隔天）與同業族群（同產業連動、族群動能）。

全部由價格資料自動算出，不靠人工整理的供應鏈名單：
- 連動：台股第 t 天報酬 vs 美股「t 之前最後一個交易日」報酬。前 70% 期間估計、後 30% 期間驗證，
  兩段都成立才列為「有效連動」，避免把巧合當規律。
- 同業：證交所產業分類中成交金額最大的幾檔，算同日報酬相關與近期強弱。
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pandas as pd

from .data import yahoo_history

US = {"TSM": "台積電 ADR", "NVDA": "輝達", "AMD": "超微", "AVGO": "博通", "MU": "美光", "AAPL": "蘋果",
      "MSFT": "微軟", "GOOGL": "Google", "AMZN": "亞馬遜", "META": "Meta", "TSLA": "特斯拉", "QCOM": "高通",
      "INTC": "英特爾", "^SOX": "費城半導體指數", "^IXIC": "那斯達克指數"}
_pool = ThreadPoolExecutor(max_workers=4)


def _hist(sym):
    try:
        df, _ = yahoo_history(sym, "5y")
        return df
    except Exception:  # noqa: BLE001
        return None


def _rets(df):
    r = np.log(df["close"]).diff().dropna()
    return r[r.abs() < 0.25]  # 去掉資料錯誤造成的極端值


def overnight(tw_df: pd.DataFrame, window: int = 750) -> dict:
    tw = _rets(tw_df).iloc[-window:]
    us_dfs = dict(zip(US, _pool.map(_hist, US)))
    rows = []
    for sym, df in us_dfs.items():
        if df is None or len(df) < 300:
            continue
        ur = _rets(df)
        # 台股第 t 天對應「t 之前」最後一個美股交易日（美股收盤在台股開盤前）
        a = pd.merge_asof(pd.DataFrame({"date": tw.index, "tw": tw.values}),
                          pd.DataFrame({"udate": ur.index, "us": ur.values}),
                          left_on="date", right_on="udate", allow_exact_matches=False).dropna()
        if len(a) < 200:
            continue
        cut = int(len(a) * 0.7)
        tr, te = a.iloc[:cut], a.iloc[cut:]
        c_tr, c_te = tr.tw.corr(tr.us), te.tw.corr(te.us)
        beta = float(np.polyfit(a.us, a.tw, 1)[0])
        rows.append({"sym": sym, "name": US[sym], "corr": float(a.tw.corr(a.us)), "corr_train": float(c_tr),
                     "corr_test": float(c_te), "beta": beta, "n": int(len(a)),
                     "valid": bool(c_tr > 0.15 and c_te > 0.10),
                     "last_date": df.index[-1].date().isoformat(), "last_ret": float(np.exp(ur.iloc[-1]) - 1)})
    rows.sort(key=lambda r: -r["corr"])
    tw_last = tw_df.index[-1].date().isoformat()
    best = next((r for r in rows if r["valid"]), None)
    out = {"rows": rows[:6], "tw_last": tw_last, "best": best}
    if best:
        pending = best["last_date"] >= tw_last  # 美股最新收盤在台股最新收盤之後 -> 影響下一個交易日
        exp = best["beta"] * np.log1p(best["last_ret"])
        out["expect"] = {"pending": pending, "move": float(np.expm1(exp))}
    return out


def peers(code: str, industry: str, candidates: list, tw_df: pd.DataFrame, n: int = 10) -> dict:
    """candidates: [(code, name, yahoo_symbol, trade_value)] 同產業股票。"""
    cands = sorted([c for c in candidates if c[0] != code], key=lambda c: -(c[3] or 0))[:n]
    dfs = list(_pool.map(lambda c: _hist(c[2]), cands))
    me = _rets(tw_df).iloc[-250:]
    rows = []
    for (pc, pname, _, _), df in zip(cands, dfs):
        if df is None or len(df) < 260:
            continue
        r = _rets(df)
        c = df["close"]
        rows.append({"code": pc, "name": pname, "corr": float(me.corr(r.reindex(me.index))),
                     "ret20": float(c.iloc[-1] / c.iloc[-21] - 1), "ret60": float(c.iloc[-1] / c.iloc[-61] - 1),
                     "mom12_1": float(c.iloc[-21] / c.iloc[-251] - 1)})
    rows.sort(key=lambda r: -(r["corr"] if r["corr"] == r["corr"] else -9))
    c = tw_df["close"]
    mine = {"ret20": float(c.iloc[-1] / c.iloc[-21] - 1), "ret60": float(c.iloc[-1] / c.iloc[-61] - 1),
            "mom12_1": float(c.iloc[-21] / c.iloc[-251] - 1) if len(c) > 251 else None}
    out = {"industry": industry, "rows": rows, "me": mine}
    if rows:
        med = float(np.median([r["mom12_1"] for r in rows]))
        med60 = float(np.median([r["ret60"] for r in rows]))
        out.update({"industry_mom": med, "industry_ret60": med60,
                    "rank60": 1 + sum(1 for r in rows if r["ret60"] > mine["ret60"]), "count": len(rows) + 1})
    return out
