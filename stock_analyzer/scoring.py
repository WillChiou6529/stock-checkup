"""評分模型：技術面（向量化，可回測）+ 基本面 + 籌碼面 -> 綜合評分、評等、交易計畫。

每個面向 0~100 分，50 為中性。每條規則同時記錄「今日」的加減分與理由，報告中的每句話都對應實際計分。

權重依據 validate_model.py 的橫斷面檢定：趨勢/動能/量價對未來 20~60 日相對報酬有正向預測力；
「風險控管」分數（乖離、波動、大盤位階）預測力為負，因此不計入總分，只用於部位控管與追高提醒。
基本面/籌碼沒有可回測的每日歷史，權重屬經驗設定。
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

WEIGHTS = {"trend": 0.30, "momentum": 0.20, "volume": 0.10, "fundamental": 0.22, "chips": 0.18, "risk": 0.0}
TECH_KEYS = ("trend", "momentum", "volume", "risk")
SCORED_TECH = ("trend", "momentum", "volume")
PILLAR_NAMES = {"trend": "趨勢", "momentum": "動能", "volume": "量價", "fundamental": "基本面",
                "chips": "籌碼", "risk": "風險控管"}


class Pillar:
    """向量化計分器：每條規則對整段歷史加分，同時記下最後一天的理由。"""

    def __init__(self, index, base: float):
        self.s = pd.Series(float(base), index=index)
        self.notes: list = []

    @staticmethod
    def _b(x):
        return x.fillna(False).astype(bool) if isinstance(x, pd.Series) else x

    def binary(self, valid, cond, pts, yes, no, neg=None):
        valid, cond = self._b(valid), self._b(cond)
        neg = -pts if neg is None else neg
        add = np.where(valid, np.where(cond, pts, neg), 0.0)
        self.s += add
        if bool(valid.iloc[-1]):
            t = yes if bool(cond.iloc[-1]) else no
            if t and add[-1] != 0:
                self.notes.append((float(add[-1]), t))

    def tiers(self, valid, tiers):
        """tiers: [(cond, pts, text)]，由上往下第一個成立者計分。"""
        valid = self._b(valid)
        conds = [self._b(c).to_numpy() for c, _, _ in tiers]
        add = np.where(valid, np.select(conds, [p for _, p, _ in tiers], 0.0), 0.0)
        self.s += add
        if bool(valid.iloc[-1]):
            for c, p, t in tiers:
                if bool(self._b(c).iloc[-1]):
                    if t and p:
                        self.notes.append((float(p), t))
                    break

    def result(self):
        return self.s.clip(0, 100)


class ScalarPillar:
    def __init__(self, base: float):
        self.score = float(base)
        self.notes: list = []

    def add(self, pts, text):
        self.score += pts
        if text and pts:
            self.notes.append((float(pts), text))

    def result(self):
        return float(min(max(self.score, 0), 100))


# ------------------------------------------------------------------ 技術面
def score_trend(df):
    L, c = df.iloc[-1], df["close"]
    P = Pillar(df.index, 50)
    P.binary(df.ma20.notna(), c > df.ma20, 8, f"站上月線 MA20（{L.ma20:.2f}）", f"跌破月線 MA20（{L.ma20:.2f}）")
    P.binary(df.ma60.notna(), c > df.ma60, 10, f"站上季線 MA60（{L.ma60:.2f}）", f"位於季線 MA60（{L.ma60:.2f}）之下，中期偏弱")
    P.binary(df.ma120.notna(), c > df.ma120, 5, "站上半年線 MA120", "位於半年線 MA120 之下")
    P.binary(df.ma240.notna(), c > df.ma240, 7, f"站上年線 MA240（{L.ma240:.2f}），長線多頭",
             f"位於年線 MA240（{L.ma240:.2f}）之下，長線偏空")
    P.binary(df.ma20.notna() & df.ma60.notna(), df.ma20 > df.ma60, 6, "月線在季線之上（短中期多頭排列）", "月線在季線之下（短中期空頭排列）")
    P.binary(df.ma60.notna() & df.ma120.notna(), df.ma60 > df.ma120, 4, "季線在半年線之上", "季線在半年線之下")
    P.binary(df.ma60_slope.notna(), df.ma60_slope > 0, 5, f"季線上揚（10 日 {L.ma60_slope:+.1%}）",
             f"季線下彎（10 日 {L.ma60_slope:+.1%}）")
    P.tiers(df.adx.notna(), [
        ((df.adx > 25) & (df.pdi > df.mdi), 5, f"ADX {L.adx:.0f}：多方趨勢明確"),
        ((df.adx > 25) & (df.mdi > df.pdi), -5, f"ADX {L.adx:.0f}：空方趨勢明確"),
    ])
    m = df.mom12_1
    P.tiers(m.notna(), [
        (m > 0.20, 8, f"中期動能強：過去 12 個月（扣除近 1 月）上漲 {L.mom12_1:+.0%}"),
        (m > 0, 4, f"中期動能正向（12-1 月 {L.mom12_1:+.0%}）"),
        (m < -0.20, -8, f"中期動能弱：過去 12 個月（扣除近 1 月）下跌 {L.mom12_1:+.0%}"),
        (m <= 0, -4, f"中期動能負向（12-1 月 {L.mom12_1:+.0%}）"),
    ])
    return P


def score_momentum(df):
    L = df.iloc[-1]
    P = Pillar(df.index, 50)
    r = df.rsi
    P.tiers(r.notna(), [
        (r > 80, -5, f"RSI {L.rsi:.0f} 過熱（>80），短線追價風險高"),
        (r > 70, 5, f"RSI {L.rsi:.0f} 強勢區"),
        (r >= 55, 10, f"RSI {L.rsi:.0f} 多方健康區（55~70）"),
        (r >= 45, 0, None),
        (r >= 30, -8, f"RSI {L.rsi:.0f} 偏弱"),
        (r < 30, -5, f"RSI {L.rsi:.0f} 超賣（<30），留意跌深反彈但趨勢未轉"),
    ])
    h = df.macd_hist
    P.binary(h.notna(), h > 0, 8, "MACD 柱狀體翻正（多方）", "MACD 柱狀體為負（空方）")
    P.binary(h.notna() & h.shift(1).notna(), h > h.shift(1), 4, "MACD 柱狀體擴張/收斂向上", "MACD 柱狀體走弱")
    P.binary(df.dif.notna(), df.dif > 0, 5, "DIF 在零軸之上", "DIF 在零軸之下")
    P.binary(df.k.notna(), df.k > df.d, 5, f"KD 多方（K {L.k:.0f} > D {L.d:.0f}）", f"KD 空方（K {L.k:.0f} < D {L.d:.0f}）")
    P.tiers(df.k.notna(), [
        ((df.k > 80) & (df.k < df.d), -5, "KD 高檔死亡交叉"),
        ((df.k < 20) & (df.k > df.d), 5, "KD 低檔黃金交叉"),
    ])
    P.tiers(df.ret20.notna(), [
        (df.ret20 > 0.05, 5, f"近 20 日上漲 {L.ret20:+.1%}"),
        (df.ret20 < -0.05, -5, f"近 20 日下跌 {L.ret20:+.1%}"),
    ])
    if "rs60" in df:
        P.tiers(df.rs60.notna(), [
            (df.rs60 > 0.05, 8, f"近 60 日強於大盤 {L.rs60:+.1%}（相對強勢股）"),
            (df.rs60 < -0.05, -8, f"近 60 日落後大盤 {L.rs60:+.1%}（相對弱勢）"),
        ])
    return P


def score_volume(df):
    L, c = df.iloc[-1], df["close"]
    P = Pillar(df.index, 50)
    pr20 = c - c.shift(20)
    ok = df.obv_chg20.notna() & pr20.notna()
    P.tiers(ok, [
        ((df.obv_chg20 > 0) & (pr20 > 0), 10, "OBV 與股價同步走高（價量齊揚）"),
        ((df.obv_chg20 > 0) & (pr20 <= 0), 8, "股價未漲但 OBV 上升（疑似有資金吸籌）"),
        ((df.obv_chg20 <= 0) & (pr20 > 0), -10, "股價上漲但 OBV 下降（價量背離）"),
        ((df.obv_chg20 <= 0) & (pr20 <= 0), -8, "OBV 與股價同步走低（量能退潮）"),
    ])
    chg = c.diff()
    P.tiers(df.vol_ratio.notna(), [
        ((chg > 0) & (df.vol_ratio > 1.5), 10, f"今日帶量上漲（量為 20 日均量 {L.vol_ratio:.1f} 倍）"),
        ((chg < 0) & (df.vol_ratio > 1.5), -10, f"今日爆量下跌（量為 20 日均量 {L.vol_ratio:.1f} 倍），留意出貨"),
    ])
    P.tiers(df.updown_vol.notna(), [
        (df.updown_vol > 1.5, 10, f"近 10 日上漲日成交量為下跌日 {L.updown_vol:.1f} 倍（買盤主導）"),
        (df.updown_vol < 0.67, -10, f"近 10 日下跌日量能較大（上/下量比 {L.updown_vol:.2f}，賣壓主導）"),
    ])
    brk = (df.high >= df.hi20.shift(1)) & (df.vol_ratio > 1.2)
    P.binary(df.hi20.shift(1).notna(), brk, 10, "帶量突破 20 日高點", None, neg=0)
    return P


def score_risk(df):
    L = df.iloc[-1]
    P = Pillar(df.index, 70)
    b = df.bias20
    P.tiers(b.notna(), [
        (b > 0.15, -20, f"月線乖離 {L.bias20:+.1%} 過大，短線易回檔"),
        (b > 0.10, -10, f"月線乖離 {L.bias20:+.1%} 偏高"),
        (b < -0.15, -5, f"月線負乖離 {L.bias20:+.1%}（跌深，但勿輕易接刀）"),
    ])
    v = df.vol60
    P.tiers(v.notna(), [
        (v > 0.60, -15, f"年化波動 {L.vol60:.0%} 極高，部位需縮小"),
        (v > 0.40, -8, f"年化波動 {L.vol60:.0%} 偏高"),
        (v < 0.25, 5, f"年化波動 {L.vol60:.0%} 溫和"),
    ])
    P.binary(df.dd252.notna(), df.dd252 < -0.30, -10, f"距 52 週高點 {L.dd252:+.0%}，仍在大幅修正中", None, neg=0)
    P.binary(df.rsi.notna(), df.rsi > 80, -10, "RSI 過熱", None, neg=0)
    P.binary(df.bb_pctb.notna(), df.bb_pctb > 1.0, -5, "收盤在布林上軌之外", None, neg=0)
    if "idx_ma60" in df:
        ic = df.idx_close
        P.tiers(df.idx_ma60.notna(), [
            ((ic < df.idx_ma60) & (ic < df.idx_ma240), -15, "大盤跌破季線與年線（空頭市場，降低持股）"),
            (ic < df.idx_ma60, -10, "大盤跌破季線（系統性風險升高）"),
            ((ic > df.idx_ma60) & (df.idx_ma20 > df.idx_ma60), 5, "大盤在季線之上且多頭排列"),
        ])
    return P


def technical(df):
    pillars = {"trend": score_trend(df), "momentum": score_momentum(df),
               "volume": score_volume(df), "risk": score_risk(df)}
    series = {k: p.result() for k, p in pillars.items()}
    w = sum(WEIGHTS[k] for k in SCORED_TECH)
    tech = sum(series[k] * WEIGHTS[k] for k in SCORED_TECH) / w
    return pillars, series, tech


# ------------------------------------------------------------------ 基本面
def score_fundamental(price, val: dict, rev: dict, eps: dict):
    if not (val or rev or eps):
        return None
    P = ScalarPillar(50)
    yoy, cum, mom = rev.get("yoy"), rev.get("cum_yoy"), rev.get("mom")
    per = rev.get("period", "")
    if yoy is not None:
        if yoy > 30:
            P.add(15, f"{per} 月營收年增 {yoy:+.1f}%，成長強勁")
        elif yoy > 10:
            P.add(8, f"{per} 月營收年增 {yoy:+.1f}%")
        elif yoy > 0:
            P.add(2, f"{per} 月營收年增 {yoy:+.1f}%（溫和）")
        elif yoy > -10:
            P.add(-5, f"{per} 月營收年減 {yoy:.1f}%")
        else:
            P.add(-12, f"{per} 月營收年減 {yoy:.1f}%，衰退明顯")
    if cum is not None:
        if cum > 20:
            P.add(10, f"今年累計營收年增 {cum:+.1f}%")
        elif cum > 5:
            P.add(5, f"今年累計營收年增 {cum:+.1f}%")
        elif cum < -5:
            P.add(-8, f"今年累計營收年減 {cum:.1f}%")
    if mom is not None:
        if mom > 10:
            P.add(3, f"營收月增 {mom:+.1f}%")
        elif mom < -15:
            P.add(-3, f"營收月減 {mom:.1f}%（留意是否為季節性）")

    pe = val.get("pe")
    growth = cum if cum is not None else yoy
    if val:
        if pe is None or pe <= 0:
            P.add(-12, "近四季虧損或無本益比")
        else:
            peg = pe / growth if growth and growth > 0 else None
            if pe < 10:
                P.add(10, f"本益比 {pe:.1f} 倍，偏低")
            elif pe < 15:
                P.add(6, f"本益比 {pe:.1f} 倍，合理偏低")
            elif pe < 22:
                P.add(2, f"本益比 {pe:.1f} 倍，合理")
            elif pe < 30:
                if peg is not None and peg < 1:
                    P.add(3, f"本益比 {pe:.1f} 倍偏高，但成長率足以支撐（PEG≈{peg:.2f}）")
                else:
                    P.add(-3, f"本益比 {pe:.1f} 倍，偏高")
            else:
                if peg is not None and peg < 1:
                    P.add(-2, f"本益比 {pe:.1f} 倍很高，靠高成長撐（PEG≈{peg:.2f}），成長放緩即有修正風險")
                else:
                    P.add(-10, f"本益比 {pe:.1f} 倍，評價昂貴")
        y = val.get("yield")
        if y is not None:
            if y >= 5:
                P.add(6, f"殖利率 {y:.2f}%，高殖利率")
            elif y >= 3.5:
                P.add(3, f"殖利率 {y:.2f}%")
        pb = val.get("pb")
        if pb is not None and 0 < pb < 1 and pe and pe > 0:
            P.add(3, f"股價淨值比 {pb:.2f}（低於淨值且仍獲利）")

    e, q = eps.get("eps_ytd"), eps.get("quarter")
    if e is not None and q:
        ann = e * 4 / q
        if ann <= 0:
            P.add(-6, f"今年前 {q} 季 EPS {e:.2f} 元（虧損）")
        elif pe and pe > 0 and price:
            implied = price / ann
            if implied < pe * 0.9:
                P.add(6, f"今年前 {q} 季 EPS {e:.2f} 元，年化本益比 {implied:.1f} 倍低於近四季 {pe:.1f} 倍（獲利成長中）")
            elif implied > pe * 1.1:
                P.add(-6, f"今年前 {q} 季 EPS {e:.2f} 元，年化本益比 {implied:.1f} 倍高於近四季 {pe:.1f} 倍（獲利衰退）")
    return P


# ------------------------------------------------------------------ 籌碼面
def _streak(s: pd.Series, positive=True):
    n = 0
    for v in reversed(s.tolist()):
        if (v > 0) if positive else (v < 0):
            n += 1
        else:
            break
    return n


def score_chips(inst: pd.DataFrame, margin: dict, avg_vol_lots: float, ret10: float | None):
    if inst is None or inst.empty or not avg_vol_lots:
        return None
    P = ScalarPillar(50)
    last5 = inst.tail(5)
    f5, t5, d5 = last5.foreign.sum(), last5.trust.sum(), last5.dealer.sum()
    base = avg_vol_lots * len(last5)
    fr, tr = f5 / base, t5 / base
    if fr > 0.05:
        P.add(12, f"外資近 5 日買超 {f5:,.0f} 張（占成交量 {fr:.1%}），積極布局")
    elif fr > 0.01:
        P.add(6, f"外資近 5 日買超 {f5:,.0f} 張")
    elif fr < -0.05:
        P.add(-12, f"外資近 5 日賣超 {-f5:,.0f} 張（占成交量 {-fr:.1%}），明顯調節")
    elif fr < -0.01:
        P.add(-6, f"外資近 5 日賣超 {-f5:,.0f} 張")
    if tr > 0.02:
        P.add(10, f"投信近 5 日買超 {t5:,.0f} 張（占成交量 {tr:.1%}），作帳/認養跡象")
    elif tr > 0.005:
        P.add(5, f"投信近 5 日買超 {t5:,.0f} 張")
    elif tr < -0.02:
        P.add(-8, f"投信近 5 日賣超 {-t5:,.0f} 張")
    elif tr < -0.005:
        P.add(-4, f"投信近 5 日賣超 {-t5:,.0f} 張")
    ts, fs = _streak(inst.trust), _streak(inst.foreign, positive=False)
    if ts >= 3:
        P.add(5, f"投信連續買超 {ts} 日")
    if fs >= 5:
        P.add(-5, f"外資連續賣超 {fs} 日")
    tot = inst.total.sum()
    P.add(4 if tot > 0 else -4, f"近 {len(inst)} 日三大法人合計{'買' if tot > 0 else '賣'}超 {abs(tot):,.0f} 張")

    if margin and margin.get("margin_past"):
        m0, m1 = margin["margin_past"], margin["margin"]
        chg = (m1 - m0) / m0 if m0 else 0
        if ret10 is not None:
            if chg > 0.05 and ret10 < 0:
                P.add(-8, f"融資增加 {chg:+.1%} 但股價下跌（散戶接刀，籌碼凌亂）")
            elif chg < -0.05 and ret10 > 0:
                P.add(6, f"融資減少 {chg:+.1%} 而股價上漲（籌碼沉澱、由法人接手）")
        if chg > 0.15:
            P.add(-5, f"融資兩週暴增 {chg:+.1%}，短線籌碼不穩")
        if margin.get("margin") and margin.get("short") is not None:
            sr = margin["short"] / margin["margin"] if margin["margin"] else 0
            if sr > 0.3:
                P.add(3, f"券資比 {sr:.0%} 偏高，有軋空可能")
    return P


# ------------------------------------------------------------------ 綜合
def composite(scores: dict):
    used = {k: v for k, v in scores.items() if v is not None and WEIGHTS[k] > 0}
    w = sum(WEIGHTS[k] for k in used)
    return sum(v * WEIGHTS[k] for k, v in used.items()) / w


def rating(score: float, scores: dict):
    if score >= 72:
        r, act = "偏多", "買進"
    elif score >= 60:
        r, act = "偏多", "逢回分批布局"
    elif score >= 45:
        r, act = "中性", "觀望"
    elif score >= 35:
        r, act = "偏空", "減碼／暫不進場"
    else:
        r, act = "偏空", "賣出／避開"
    caveats = []
    if scores["trend"] < 35 and r == "偏多":
        r, act = "中性", "觀望"
        caveats.append("其他面向不錯，但趨勢仍向下：等站回季線、季線走平再進場，不要逆勢摸底")
    if scores["risk"] < 35 and act in ("買進", "逢回分批布局"):
        act = "不追高，等回檔"
        caveats.append("短線乖離/過熱，現價追進的風險報酬比差，等拉回支撐再分批")
    return r, act, caveats


def tick(price: float) -> float:
    """台股升降單位。"""
    if price < 10:
        return 0.01
    if price < 50:
        return 0.05
    if price < 100:
        return 0.1
    if price < 500:
        return 0.5
    if price < 1000:
        return 1.0
    return 5.0


def round_px(p: float, tw: bool, mode="near") -> float:
    if p is None or not math.isfinite(p):
        return p
    if not tw:
        return round(p, 2)
    t = tick(p)
    f = {"near": round, "down": math.floor, "up": math.ceil}[mode]
    return round(f(p / t) * t, 2)


def trade_plan(df, price: float, tw: bool, capital: float, risk_pct: float, max_pos_pct: float):
    """以「目前價」為基準的進場/停損/目標/部位建議。

    df 價格為還原價；今天的還原價 = 實際價，因此近期價位可直接使用。
    """
    L = df.iloc[-1]
    a = float(L.atr)
    sup_cands = {"月線 MA20": L.ma20, "季線 MA60": L.ma60, "20 日低點": L.lo20, "60 日低點": L.lo60,
                 "布林下軌": L.bb_lo, "年線 MA240": L.ma240}
    res_cands = {"20 日高點": L.hi20, "60 日高點": L.hi60, "52 週高點": L.hi252, "布林上軌": L.bb_up,
                 "季線 MA60": L.ma60, "年線 MA240": L.ma240}
    sups = sorted([(v, k) for k, v in sup_cands.items() if pd.notna(v) and v < price * 0.995], reverse=True)
    ress = sorted([(v, k) for k, v in res_cands.items() if pd.notna(v) and v > price * 1.005])

    # 只有 3 ATR 內的支撐才拿來設停損/進場，太遠的支撐沒有操作意義
    near = [v for v, _ in sups if v >= price - 3 * a]
    s1 = near[0] if near else None
    if s1 is not None:
        stop = max(price - 3 * a, min(price - 1.5 * a, s1 - 0.5 * a))
    else:
        stop = price - 2.5 * a
    risk = price - stop
    t1 = ress[0][0] if ress and ress[0][0] >= price + risk else price + 2 * risk
    t2 = max(price + 3 * risk, ress[1][0] if len(ress) > 1 else 0)
    rr = (t1 - price) / risk if risk > 0 else None

    # 理想進場區：靠近支撐就是「支撐~現價」；離支撐較遠則等回測支撐；無近支撐則等拉回 1 ATR
    if s1 is not None and price - s1 <= a:
        entry = (s1, price)
    elif s1 is not None:
        entry = (s1, s1 + 0.5 * a)
    else:
        entry = (price - a, price - 0.3 * a)

    lot = 1000 if tw else 1
    sh_risk = capital * risk_pct / 100 / risk if risk > 0 else 0
    sh_cap = capital * max_pos_pct / 100 / price
    shares = int(min(sh_risk, sh_cap))
    rp = lambda x, m="near": round_px(float(x), tw, m)  # noqa: E731

    def merge(levels):
        out = []
        for v, k in levels:
            v = rp(v)
            if out and out[-1][0] == v:
                out[-1] = (v, out[-1][1] + "／" + k)
            else:
                out.append((v, k))
        return out[:3]

    return {
        "atr": a, "supports": merge(sups), "resistances": merge(ress),
        "entry": (rp(entry[0], "up"), rp(entry[1], "down")), "stop": rp(stop, "down"),
        "target1": rp(t1), "target2": rp(t2), "rr": rr, "risk_per_share": risk,
        "shares": shares, "lots": shares / lot if tw else None,
        "position_value": shares * price, "limited_by": "單筆風險" if sh_risk <= sh_cap else "單一持股上限",
        "chandelier": rp(L.chandelier, "down") if pd.notna(L.chandelier) else None,
    }


def holder_advice(price, plan, score, scores, holding: dict | None, df):
    if not holding:
        return None
    cost = holding.get("cost")
    sh = holding.get("shares")
    L = df.iloc[-1]
    trail = plan["chandelier"]
    hard = plan["stop"]
    out = {"cost": cost, "shares": sh}
    if cost:
        out["pnl_pct"] = price / cost - 1
        if sh:
            out["pnl"] = (price - cost) * sh
    if trail and price < trail:
        out["advice"] = f"已跌破移動停損 {trail}（22 日高點 − 3ATR），建議出場或至少減碼一半"
    elif pd.notna(L.ma60) and price < L.ma60 and scores["trend"] < 40:
        out["advice"] = f"跌破季線且趨勢轉弱，建議減碼；剩餘部位停損設 {hard}"
    elif score >= 60:
        out["advice"] = f"續抱。移動停利設 {trail}，跌破再出場；不需預設賣點讓獲利奔跑"
    elif score >= 45:
        out["advice"] = f"續抱觀察，停損設 {hard}；反彈至壓力 {plan['resistances'][0][0] if plan['resistances'] else plan['target1']} 可考慮調節"
    else:
        r = plan["resistances"][0][0] if plan["resistances"] else None
        out["advice"] = "評分偏空，建議減碼" + (f"，或反彈至 {r} 附近調節" if r else "")
    return out
