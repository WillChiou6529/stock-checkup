"""下載「千張大戶」驗證用資料：成交金額最大的 120 檔，近一年每 4 週一筆（集保只提供一年歷史）。結果永久快取。"""
from __future__ import annotations

import datetime as dt
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from stock_analyzer.data import TWMarket  # noqa: E402
from stock_analyzer.holders import TDCC  # noqa: E402

OUT = os.path.join(ROOT, "research", "data", "tdcc_sample.json")


def universe(n=120):
    tw = TWMarket([])
    tw._load_universe()
    rows = [(c, v["value"] or 0) for src in (tw._listed, tw._otc) for c, v in src.items()
            if len(c) == 4 and c.isdigit() and not c.startswith("0")]
    return [c for c, _ in sorted(rows, key=lambda x: -x[1])[:n]]


def main():
    codes = universe()
    t = TDCC()
    dates = t.dates()[::4]
    print(f"{len(codes)} 檔 × {len(dates)} 期", flush=True)
    data = {}
    for i, d in enumerate(dates, 1):
        for c in codes:
            w = t.week(c, d)
            if w:
                data.setdefault(c, {})[d] = w
        print(f"[{dt.datetime.now():%H:%M:%S}] {i}/{len(dates)} {d}", flush=True)
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump({"codes": codes, "dates": dates, "data": data}, f)
    print("done", OUT)


if __name__ == "__main__":
    main()
