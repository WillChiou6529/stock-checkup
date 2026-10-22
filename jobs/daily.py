"""每日排程（GitHub Actions 每個交易日 21:30 執行，也可本機手動跑）：

1. 累積內部人持股轉讓申報、重大訊息 -> data/insider.csv、data/announcements.csv
2. 對追蹤池（權值股 + 預設自選 + 當日選股）做完整健診，記錄評分快照 -> data/signals.csv
3. 計算過去每個建議的實際表現 -> data/track.json

用法：python -m jobs.daily
"""
from __future__ import annotations

import csv
import datetime as dt
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from stock_analyzer import events, track  # noqa: E402
from stock_analyzer.analyzer import Context, analyze, is_tw  # noqa: E402
from stock_analyzer.universe import BASKET  # noqa: E402


def log(msg):
    print(f"[{dt.datetime.now():%H:%M:%S}] {msg}", flush=True)


def bootstrap_from_cli(rows: list) -> list:
    """第一次執行時，把本機命令列版留下的 signals_history.csv 併入（上線前的實際紀錄）。"""
    path = os.path.join(ROOT, "signals_history.csv")
    if not os.path.exists(path):
        return rows
    have = {(r["date"], r["code"]) for r in rows}
    with open(path, encoding="utf-8-sig", newline="") as f:
        for r in csv.DictReader(f):
            if (r["date"], r["code"]) not in have:
                r["source"] = "命令列"
                rows.append(r)
    return rows


def main():
    with open(os.path.join(ROOT, "config.json"), encoding="utf-8") as f:
        cfg = json.load(f)
    cfg["holdings"] = {}

    n1, n2 = events.collect_daily()
    log(f"內部人申報 +{n1}、重大訊息 +{n2}")

    ctx = Context(cfg)
    sc = cfg.get("scan") or {}
    try:
        picks = [c for c, _ in ctx.tw.scan_candidates(int(sc.get("top_n", 6)) + 2, float(sc.get("min_trade_value_million", 100)))]
    except Exception as e:  # noqa: BLE001
        log(f"選股掃描失敗：{e}")
        picks = []
    source = {}
    for c in BASKET:
        source[c] = "追蹤池"
    for c in cfg.get("watchlist", []):
        source.setdefault(c, "自選")
    for c in picks:
        source[c] = "今日選股"

    # 先把台股法人/融資資料抓好（單執行緒，避免官方網站節流），再平行分析
    ctx.tw.recent_inst_days("listed", 10), ctx.tw.recent_inst_days("otc", 10)

    def one(c):
        try:
            return analyze(c, ctx)
        except Exception as e:  # noqa: BLE001
            log(f"{c} 失敗：{e}")
            return None

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = [a for a in pool.map(one, list(source)) if a]
    log(f"完成 {len(results)}/{len(source)} 檔健診")

    rows = bootstrap_from_cli(track.read_signals())
    for a in results:
        rows.append(track.row_from(a, a["date"].isoformat(), source[a["code"]]))
    track.save_signals(rows)

    symbols = {a["code"]: a["symbol"] for a in results}
    for r in rows:
        c = r["code"]
        if c not in symbols:
            if is_tw(c):
                hit = ctx.tw.resolve(c)
                if hit:
                    symbols[c] = hit[0]
            else:
                symbols[c] = c
    data = track.build_track(symbols)
    log(f"績效追蹤：{data['summary'].get('n', 0)} 筆建議，{data['summary'].get('days', 0)} 個交易日")


if __name__ == "__main__":
    main()
