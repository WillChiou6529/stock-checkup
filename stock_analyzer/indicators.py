"""技術指標（全部向量化，輸入為 OHLCV DataFrame）。"""
from __future__ import annotations

import numpy as np
import pandas as pd


def sma(s: pd.Series, n: int) -> pd.Series:
    return s.rolling(n, min_periods=n).mean()


def ema(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(span=n, adjust=False).mean()


def wilder(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(alpha=1 / n, adjust=False).mean()


def rsi(close: pd.Series, n: int = 14) -> pd.Series:
    d = close.diff()
    up = wilder(d.clip(lower=0), n)
    dn = wilder(-d.clip(upper=0), n)
    tot = up + dn
    r = 100 * up / tot.replace(0, np.nan)
    r = r.fillna(50)
    r.iloc[:n] = np.nan
    return r


def kd(df: pd.DataFrame, n: int = 9):
    """台灣常用 KD(9,3,3)：K = 2/3 K' + 1/3 RSV，起始 50。"""
    lo = df["low"].rolling(n, min_periods=n).min()
    hi = df["high"].rolling(n, min_periods=n).max()
    rsv = ((df["close"] - lo) / (hi - lo).replace(0, np.nan) * 100).to_numpy()
    k = np.full(len(df), np.nan)
    d = np.full(len(df), np.nan)
    pk = pd_ = 50.0
    for i, v in enumerate(rsv):
        if np.isnan(v):
            continue
        pk = pk * 2 / 3 + v / 3
        pd_ = pd_ * 2 / 3 + pk / 3
        k[i], d[i] = pk, pd_
    return pd.Series(k, index=df.index), pd.Series(d, index=df.index)


def atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    pc = df["close"].shift(1)
    tr = pd.concat([df["high"] - df["low"], (df["high"] - pc).abs(), (df["low"] - pc).abs()], axis=1).max(axis=1)
    return wilder(tr, n)


def adx(df: pd.DataFrame, n: int = 14):
    up = df["high"].diff()
    dn = -df["low"].diff()
    pdm = pd.Series(np.where((up > dn) & (up > 0), up, 0.0), index=df.index)
    mdm = pd.Series(np.where((dn > up) & (dn > 0), dn, 0.0), index=df.index)
    a = atr(df, n)
    pdi = 100 * wilder(pdm, n) / a
    mdi = 100 * wilder(mdm, n) / a
    dx = 100 * (pdi - mdi).abs() / (pdi + mdi).replace(0, np.nan)
    return wilder(dx.fillna(0), n), pdi, mdi


def add_indicators(df: pd.DataFrame, index_close: pd.Series | None = None) -> pd.DataFrame:
    df = df.copy()
    c, v = df["close"], df["volume"]
    for n in (5, 10, 20, 60, 120, 240):
        df[f"ma{n}"] = sma(c, n)
    df["ma60_slope"] = df["ma60"] / df["ma60"].shift(10) - 1
    df["ma20_slope"] = df["ma20"] / df["ma20"].shift(5) - 1

    df["dif"] = ema(c, 12) - ema(c, 26)
    df["dea"] = ema(df["dif"], 9)
    df["macd_hist"] = df["dif"] - df["dea"]

    df["rsi"] = rsi(c, 14)
    df["k"], df["d"] = kd(df)

    std = c.rolling(20, min_periods=20).std(ddof=0)
    df["bb_up"] = df["ma20"] + 2 * std
    df["bb_lo"] = df["ma20"] - 2 * std
    df["bb_pctb"] = (c - df["bb_lo"]) / (df["bb_up"] - df["bb_lo"]).replace(0, np.nan)
    df["bb_width"] = (df["bb_up"] - df["bb_lo"]) / df["ma20"]

    df["atr"] = atr(df, 14)
    df["adx"], df["pdi"], df["mdi"] = adx(df, 14)

    df["vol_ma20"] = sma(v, 20)
    df["vol_ratio"] = v / df["vol_ma20"].replace(0, np.nan)
    df["obv"] = (np.sign(c.diff()).fillna(0) * v).cumsum()
    df["obv_chg20"] = df["obv"] - df["obv"].shift(20)
    up_day = c.diff() > 0
    vol_up10 = v.where(up_day, 0).rolling(10).sum()
    vol_dn10 = v.where(~up_day, 0).rolling(10).sum()
    df["updown_vol"] = vol_up10 / vol_dn10.replace(0, np.nan)

    for n in (5, 20, 60, 120, 240):
        df[f"ret{n}"] = c / c.shift(n) - 1
    # 中期動能：過去 12 個月扣掉最近 1 個月（避開短期反轉）
    df["mom12_1"] = c.shift(20) / c.shift(250) - 1
    df["bias20"] = c / df["ma20"] - 1
    df["bias60"] = c / df["ma60"] - 1
    logret = np.log(c).diff()
    df["vol20"] = logret.rolling(20).std() * np.sqrt(252)
    df["vol60"] = logret.rolling(60).std() * np.sqrt(252)
    df["hi20"] = df["high"].rolling(20).max()
    df["lo20"] = df["low"].rolling(20).min()
    df["hi60"] = df["high"].rolling(60).max()
    df["lo60"] = df["low"].rolling(60).min()
    df["hi252"] = df["high"].rolling(252, min_periods=120).max()
    df["lo252"] = df["low"].rolling(252, min_periods=120).min()
    df["dd252"] = c / df["hi252"] - 1
    df["chandelier"] = df["high"].rolling(22).max() - 3 * df["atr"]

    if index_close is not None:
        ic = index_close.reindex(df.index).ffill()
        df["idx_close"] = ic
        df["idx_ma60"] = sma(ic, 60)
        df["idx_ma20"] = sma(ic, 20)
        df["idx_ma240"] = sma(ic, 240)
        df["rs60"] = df["ret60"] - (ic / ic.shift(60) - 1)
    return df
