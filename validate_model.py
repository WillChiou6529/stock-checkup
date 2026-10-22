"""模型驗證：在一籃子台股上做「橫斷面」檢定，結果寫入 model_card.json 供報告引用。

橫斷面 IC：每個交易日把所有股票依分數排序，看分數高低和之後 N 日報酬排序的相關係數，再取平均。
它回答「同一天，分數高的股票是否比分數低的股票表現好」—— 這才是選股模型該驗證的問題。

注意：
- 樣本為目前仍上市的權值/熱門股，有存活者偏差。
- t 值以每 N 日抽一次樣本計算，避免重疊報酬高估顯著性。
- 只能驗證技術面；基本面/籌碼沒有可取得的每日歷史。

用法：python validate_model.py
"""
from __future__ import annotations

import datetime as dt
import json
import os
import sys

import numpy as np
import pandas as pd

from stock_analyzer import scoring
from stock_analyzer.data import yahoo_history
from stock_analyzer.indicators import add_indicators

BASKET = """2330 2317 2454 2308 2382 2412 2881 2882 2891 1301 1303 2002 2603 3711 2303 3008 2357 1216 2207 5880
2886 3034 2379 6505 2395 2884 2885 2892 2880 2887 2890 5876 2801 1101 1102 1326 1402 2105 2201 2301
2324 2327 2344 2345 2353 2376 2377 2383 2408 2409 2474 2492 2609 2615 2618 2912 3017 3037 3045 3231
3443 3481 3661 4904 4938 6415 6669 8046 9910 1590 2059 3533 2360 5871 2368""".split()
HORIZONS = (20, 60)
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "model_card.json")


def daily_ic(frame, col, fwd):
    def one(x):
        m = x[col].notna() & x[fwd].notna()
        if m.sum() < 20:
            return np.nan
        return np.corrcoef(x.loc[m, col].rank(), x.loc[m, fwd].rank())[0, 1]
    return frame.groupby("date").apply(one).dropna()


def main():
    twii, _ = yahoo_history("^TWII", "5y")
    parts = []
    for i, c in enumerate(BASKET, 1):
        try:
            raw, _ = yahoo_history(c + ".TW", "5y")
        except Exception as e:  # noqa: BLE001
            print(f"  略過 {c}: {e}")
            continue
        if len(raw) < 500:
            continue
        df = add_indicators(raw, twii["close"])
        _, s, tech = scoring.technical(df)
        f = pd.DataFrame({k: v for k, v in s.items()})
        f["tech"] = tech
        for h in HORIZONS:
            f[f"fwd{h}"] = df["close"].shift(-h) / df["close"] - 1
        f = f.iloc[250:]
        f["code"] = c
        parts.append(f)
        print(f"\r  載入 {i}/{len(BASKET)}", end="", flush=True)
    print()
    allf = pd.concat(parts)
    allf.index.name = "date"
    allf = allf.reset_index()

    card = {"generated": dt.date.today().isoformat(), "stocks": int(allf.code.nunique()),
            "start": str(allf.date.min().date()), "end": str(allf.date.max().date()), "results": {}}
    for h in HORIZONS:
        fwd = f"fwd{h}"
        res = {}
        for col in ("tech", "trend", "momentum", "volume", "risk"):
            s = daily_ic(allf, col, fwd)
            ss = s.iloc[::h]
            t = float(ss.mean() / ss.std() * np.sqrt(len(ss))) if len(ss) > 2 and ss.std() > 0 else None
            res[col] = {"ic": float(s.mean()), "t": t, "hit": float((s > 0).mean())}
        # 五分位：每天依技術分分 5 組，看各組之後平均報酬（扣掉當天全體平均 = 相對表現）
        d = allf.dropna(subset=["tech", fwd]).copy()
        d["q"] = d.groupby("date")["tech"].transform(lambda x: pd.qcut(x.rank(method="first"), 5, labels=False))
        d["rel"] = d[fwd] - d.groupby("date")[fwd].transform("mean")
        res["quintiles"] = [float(v) for v in d.groupby("q")["rel"].mean().sort_index()]
        card["results"][str(h)] = res

    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(card, f, ensure_ascii=False, indent=2)

    print(f"\n樣本：{card['stocks']} 檔台股，{card['start']} ~ {card['end']}")
    for h in HORIZONS:
        r = card["results"][str(h)]
        print(f"\n未來 {h} 日：")
        for k in ("tech", "trend", "momentum", "volume", "risk"):
            v = r[k]
            print(f"  {scoring.PILLAR_NAMES.get(k, '技術總分'):6s} IC {v['ic']:+.3f}  t {v['t'] or 0:+.2f}  IC>0 天數 {v['hit']:.0%}")
        print("  技術分五分位（低→高）相對報酬：" + "  ".join(f"{x:+.2%}" for x in r["quintiles"]))
    print(f"\n已寫入 {OUT}")


if __name__ == "__main__":
    sys.exit(main())
