"""每日股票分析 主程式。

用法：
  python analyze.py                 分析 config.json 的自選股 + 全市場掃描
  python analyze.py 2330 6488 NVDA  只分析指定股票（不掃描）
  python analyze.py --no-scan       不做全市場掃描
  python analyze.py --no-open       產生報告但不自動開啟瀏覽器
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import os
import sys
import time

from stock_analyzer.analyzer import Context, analyze, market_overview, is_tw
from stock_analyzer.http import purge_cache
from stock_analyzer.report import render

ROOT = os.path.dirname(os.path.abspath(__file__))
HISTORY = os.path.join(ROOT, "signals_history.csv")
HIST_COLS = ["date", "code", "name", "price", "score", "trend", "momentum", "volume", "fundamental", "chips",
             "risk", "rating", "action", "stop", "target1"]


def load_config(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def load_history():
    if not os.path.exists(HISTORY):
        return []
    with open(HISTORY, encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def save_history(rows, results, today):
    keep = [r for r in rows if not (r["date"] == today and r["code"] in {a["code"] for a in results})]
    for a in results:
        s = a["scores"]
        f = lambda v: "" if v is None else f"{v:.1f}"  # noqa: E731
        keep.append({"date": today, "code": a["code"], "name": a["name"], "price": f"{a['price']:.2f}",
                     "score": f(a["score"]), "trend": f(s["trend"]), "momentum": f(s["momentum"]),
                     "volume": f(s["volume"]), "fundamental": f(s.get("fundamental")), "chips": f(s.get("chips")),
                     "risk": f(s["risk"]), "rating": a["rating"], "action": a["action"],
                     "stop": a["plan"]["stop"], "target1": a["plan"]["target1"]})
    with open(HISTORY, "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=HIST_COLS)
        w.writeheader()
        w.writerows(keep)


def main():
    try:
        sys.stdout.reconfigure(errors="replace")
    except AttributeError:
        pass
    ap = argparse.ArgumentParser(description="每日股票分析")
    ap.add_argument("codes", nargs="*", help="股票代號（台股如 2330，美股如 NVDA）")
    ap.add_argument("--config", default=os.path.join(ROOT, "config.json"))
    ap.add_argument("--no-scan", action="store_true", help="不做全市場掃描")
    ap.add_argument("--no-open", action="store_true", help="不自動開啟報告")
    ap.add_argument("--scheduled", action="store_true", help=argparse.SUPPRESS)  # 排程執行時由 bat 帶入
    args = ap.parse_args()

    cfg = load_config(args.config)
    codes = [c.strip().upper() for c in (args.codes or cfg.get("watchlist", []))]
    for c in (cfg.get("holdings") or {}):
        if not args.codes and c.upper() not in codes:
            codes.append(c.upper())

    t0 = time.time()
    print("載入大盤資料…")
    ctx = Context(cfg)
    market, flows = market_overview(ctx, any(not is_tw(c) for c in codes))
    for m in market:
        print(f"  {m['name']} {m['close']:,.2f}（{m['chg']:+.2%}）位階：{m['regime']}")

    notes = {}
    scan = cfg.get("scan") or {}
    if scan.get("enabled") and not args.codes and not args.no_scan:
        print("全市場掃描（法人買超 + 營收成長 + 估值合理）…")
        try:
            for code, why in ctx.tw.scan_candidates(int(scan.get("top_n", 6)),
                                                    float(scan.get("min_trade_value_million", 100)),
                                                    int(scan.get("days", 5))):
                notes[code] = why
                if code not in codes:
                    codes.append(code)
            print(f"  入選 {len(notes)} 檔：{'、'.join(notes) or '無'}")
        except Exception as e:  # noqa: BLE001
            print(f"  掃描失敗：{e}")

    results, errors = [], []
    for i, c in enumerate(codes, 1):
        print(f"[{i}/{len(codes)}] 分析 {c}…", end=" ", flush=True)
        try:
            a = analyze(c, ctx, notes.get(c))
            results.append(a)
            print(f"{a['name']} 總分 {a['score']:.0f} {a['rating']}／{a['action']}")
        except Exception as e:  # noqa: BLE001
            errors.append((c, str(e)))
            print(f"失敗：{e}")

    today = dt.date.today().isoformat()
    hist = load_history()
    prev = {}
    for r in hist:
        if r["date"] < today and r.get("score"):
            prev[r["code"]] = (r["date"], float(r["score"]))
    for a in results:
        a["prev"] = prev.get(a["code"])
    results.sort(key=lambda a: -a["score"])
    save_history(hist, results, today)

    path = render(results, errors, market, flows, ctx.tw.warnings, os.path.join(ROOT, "reports"),
                  os.path.join(ROOT, "model_card.json"))
    purge_cache(30)

    print("\n" + "=" * 72)
    print(f"{'代號':<8}{'名稱':<12}{'現價':>10}{'總分':>6}  {'評等':<4}{'建議'}")
    print("-" * 72)
    for a in results:
        delta = ""
        if a.get("prev"):
            delta = f"（前次 {a['prev'][1]:.0f}）"
        print(f"{a['code']:<8}{a['name'][:10]:<12}{a['price']:>10,.2f}{a['score']:>6.0f}  {a['rating']:<4}{a['action']}{delta}")
    print("=" * 72)
    print(f"報告：{path}")
    print(f"耗時 {time.time() - t0:.0f} 秒")
    if cfg.get("open_report", True) and not args.no_open:
        try:
            os.startfile(path)  # type: ignore[attr-defined]
        except (AttributeError, OSError):
            pass
    return 0 if results else 1


if __name__ == "__main__":
    sys.exit(main())
