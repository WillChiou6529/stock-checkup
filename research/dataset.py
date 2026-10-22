"""無存活者偏差的台股研究資料集（全部官方免費資料）。

- 每日全市場行情：證交所 MI_INDEX、櫃買中心每日收盤行情 —— 包含之後才下市的股票
- 除權息：證交所 TWT49U、櫃買中心 exDailyQ，用來還原股價
- 月營收歷史：公開資訊觀測站靜態頁（含產業別）
- 每月底的本益比/殖利率/淨值比、近 10 日三大法人、融資餘額

所有原始回應永久快取在 cache/，第一次建置約 1 小時（受官方網站節流限制），之後幾秒鐘。
"""
from __future__ import annotations

import datetime as dt
import os
import pickle
import re
import sys
import threading
import urllib.parse

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from stock_analyzer.data import TWMarket, _settled, num  # noqa: E402
from stock_analyzer.http import get_json, get_text  # noqa: E402

START = dt.date(2021, 6, 1)
OUT = os.path.join(ROOT, "research", "data")
STOCK_RE = re.compile(r"^[1-9]\d{3}$")  # 一般股票（排除 ETF、權證、特別股）


def log(msg):
    print(f"[{dt.datetime.now():%H:%M:%S}] {msg}", flush=True)


def weekdays(start: dt.date, end: dt.date):
    d = start
    while d <= end:
        if d.weekday() < 5:
            yield d
        d += dt.timedelta(days=1)


# ------------------------------------------------------------------ 每日行情
def _twse_day(d: dt.date):
    url = f"https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX?date={d:%Y%m%d}&type=ALLBUT0999&response=json"
    js = get_json(url, ttl=None, keep=lambda j: j.get("stat") == "OK" or _settled(d))
    if js.get("stat") != "OK":
        return None
    t = max(js.get("tables", []), key=lambda t: len(t.get("data") or []), default=None)
    if not t or not t.get("data"):
        return None
    out = {}
    for r in t["data"]:
        code = r[0].strip()
        if STOCK_RE.match(code):
            # 2成交股數 4成交金額 5開 6高 7低 8收
            out[code] = (num(r[5]), num(r[6]), num(r[7]), num(r[8]), num(r[2]), num(r[4]), r[1].strip())
    return out


def _tpex_day(d: dt.date):
    url = ("https://www.tpex.org.tw/www/zh-tw/afterTrading/otc?date=" + urllib.parse.quote(f"{d:%Y/%m/%d}")
           + "&type=EW&response=json")
    ok = lambda j: bool(j.get("tables") and j["tables"][0].get("data"))  # noqa: E731
    js = get_json(url, ttl=None, keep=lambda j: ok(j) or _settled(d))
    if not ok(js):
        return None
    out = {}
    for r in js["tables"][0]["data"]:
        code = r[0].strip()
        if STOCK_RE.match(code):
            # 2收 4開 5高 6低 7成交股數 8成交金額
            out[code] = (num(r[4]), num(r[5]), num(r[6]), num(r[2]), num(r[7]), num(r[8]), r[1].strip())
    return out


def build_panel(board: str, days):
    loader = _twse_day if board == "listed" else _tpex_day
    recs, names = {}, {}
    n = len(days)
    for i, d in enumerate(days, 1):
        try:
            day = loader(d)
        except Exception as e:  # noqa: BLE001
            log(f"{board} {d} 失敗：{e}")
            continue
        if day:
            recs[d] = day
            for c, v in day.items():
                names[c] = v[6]
        if i % 100 == 0 or i == n:
            log(f"{board} 行情 {i}/{n}")
    idx = pd.to_datetime(sorted(recs))
    codes = sorted(names)
    panel = {}
    for k, j in (("open", 0), ("high", 1), ("low", 2), ("close", 3), ("volume", 4), ("value", 5)):
        panel[k] = pd.DataFrame({c: [recs[d].get(c, (None,) * 7)[j] for d in sorted(recs)] for c in codes},
                                index=idx, dtype="float64")
    return panel, names


# ------------------------------------------------------------------ 除權息
def _roc(s: str):
    m = re.match(r"(\d{2,3})\D+(\d{1,2})\D+(\d{1,2})", s.strip())
    return dt.date(int(m.group(1)) + 1911, int(m.group(2)), int(m.group(3))) if m else None


def exrights(board: str, years):
    ev = []
    for y in years:
        if board == "listed":
            url = f"https://www.twse.com.tw/rwd/zh/exRight/TWT49U?startDate={y}0101&endDate={y}1231&response=json"
            js = get_json(url, ttl=None if y < dt.date.today().year else 86400)
            rows = js.get("data") or []
            for r in rows:   # 0日期 1代號 3除權息前收盤 4參考價
                ev.append((_roc(r[0]), r[1].strip(), num(r[3]), num(r[4])))
        else:
            url = ("https://www.tpex.org.tw/www/zh-tw/bulletin/exDailyQ?startDate=" + urllib.parse.quote(f"{y}/01/01")
                   + "&endDate=" + urllib.parse.quote(f"{y}/12/31") + "&response=json")
            js = get_json(url, ttl=None if y < dt.date.today().year else 86400)
            rows = (js.get("tables") or [{}])[0].get("data") or []
            for r in rows:   # 0日期 1代號 3前收 4參考價
                ev.append((_roc(r[0]), r[1].strip(), num(r[3]), num(r[4])))
    return [e for e in ev if e[0] and e[2] and e[3] and e[2] > 0]


def adjust(panel: dict, events) -> dict:
    """還原權息：除權息日之前的價格乘上 參考價/前收；另把超過漲跌幅限制的跳空（減資、停復牌）視為公司行動中和掉。"""
    close = panel["close"]
    f = pd.DataFrame(1.0, index=close.index, columns=close.columns)
    for d, code, prev, ref in events:
        if code in f.columns:
            ts = pd.Timestamp(d)
            pos = close.index.searchsorted(ts)
            if 0 < pos < len(close.index):
                f.iloc[pos, f.columns.get_loc(code)] *= ref / prev
    # 台股漲跌幅上限 10%，超過 10.6% 的收盤跳動一定是公司行動
    c_ff = close.ffill()
    r = c_ff / (c_ff.shift(1) * f) - 1  # 已扣除權息後的當日報酬
    jump = (r.abs() > 0.106) & close.notna() & c_ff.shift(1).notna()
    f = f.where(~jump, f * (1 + r))  # 讓該日報酬歸零
    # 累積因子：某日之後所有事件的乘積 → 用來調整該日之前的價格
    cum = f[::-1].cumprod()[::-1].shift(-1).fillna(1.0)
    adj = {k: panel[k] * cum for k in ("open", "high", "low", "close")}
    adj.update({"volume": panel["volume"], "value": panel["value"]})
    return adj


# ------------------------------------------------------------------ 月營收（含產業別）
ROW_RE = re.compile(r"<tr align=right><td align=center>(\w+)</td><td align=left>([^<]*)</td>((?:<[tT]d nowrap>[^<]*</[tT]d>){9})")
CELL_RE = re.compile(r">([^<]*)</[tT]d>")


def revenue_month(board: str, y: int, m: int):
    mk = "sii" if board == "listed" else "otc"
    url = f"https://mopsov.twse.com.tw/nas/t21/{mk}/t21sc03_{y - 1911}_{m}_0.html"
    final = dt.date(y, m, 1) < dt.date.today().replace(day=1) - dt.timedelta(days=40)
    t = get_text(url, ttl=None if final else 86400, encoding="big5", keep=lambda s: "公司代號" in s or "產業別" in s)
    out = {}
    for sec in t.split("產業別：")[1:]:
        ind = sec.split("<", 1)[0].strip()
        for code, name, cells in ROW_RE.findall(sec):
            v = [num(x) for x in CELL_RE.findall(cells)]
            # 0當月 1上月 2去年當月 3上月比較% 4去年同月% 5當月累計 6去年累計 7前期比較%
            out[code.strip()] = {"rev": v[0], "mom": v[3], "yoy": v[4], "cum_yoy": v[7], "industry": ind}
    return out


# ------------------------------------------------------------------ 每月底快照：估值、法人、融資
def pe_on(board: str, d: dt.date):
    if board == "listed":
        url = f"https://www.twse.com.tw/rwd/zh/afterTrading/BWIBBU_d?date={d:%Y%m%d}&selectType=ALL&response=json"
        js = get_json(url, ttl=None, keep=lambda j: j.get("stat") == "OK" or _settled(d))
        if js.get("stat") != "OK":
            return {}
        # 0代號 3殖利率 5本益比 6淨值比
        return {r[0].strip(): {"pe": num(r[5]), "yield": num(r[3]), "pb": num(r[6])} for r in js.get("data") or []}
    url = ("https://www.tpex.org.tw/www/zh-tw/afterTrading/peQryDate?date=" + urllib.parse.quote(f"{d:%Y/%m/%d}")
           + "&response=json")
    js = get_json(url, ttl=None, keep=lambda j: bool(j.get("tables") and j["tables"][0].get("data")) or _settled(d))
    rows = (js.get("tables") or [{}])[0].get("data") or []
    # 0代號 2本益比 5殖利率 6淨值比
    return {r[0].strip(): {"pe": num(r[2]), "yield": num(r[5]), "pb": num(r[6])} for r in rows}


def month_ends(idx: pd.DatetimeIndex, first: dt.date):
    s = pd.Series(idx, index=idx)
    me = s.groupby([idx.year, idx.month]).max()
    return [d.date() for d in me if d.date() >= first]


def snapshots(board: str, tw: TWMarket, trading: list, samples: list, lookback: int = 10):
    """每個取樣日：估值 + 前 10 個交易日法人 + 融資（現在與 10 日前）。"""
    loader = tw._t86_day if board == "listed" else tw._tpex_inst_day
    pos = {d: i for i, d in enumerate(trading)}
    out = {}
    for k, d in enumerate(samples, 1):
        i = pos[d]
        days = trading[max(0, i - lookback + 1):i + 1]
        inst = {}
        for dd in days:
            day = loader(dd) or {}
            for c, v in day.items():
                inst.setdefault(c, []).append((dd, (v[0] or 0) / 1000, (v[1] or 0) / 1000, (v[2] or 0) / 1000, (v[3] or 0) / 1000))
        m_now = tw._margin_day(d, board) or {}
        m_past = tw._margin_day(trading[max(0, i - lookback)], board) or {}
        out[d] = {"pe": pe_on(board, d), "inst": inst, "margin": m_now, "margin_past": m_past}
        log(f"{board} 月底快照 {k}/{len(samples)}（{d}）")
    return out


def build():
    os.makedirs(OUT, exist_ok=True)
    end = dt.date.today() - dt.timedelta(days=1)
    days = list(weekdays(START, end))
    tw = TWMarket([])
    result = {}

    def job_market(board):
        panel, names = build_panel(board, days)
        ev = exrights(board, range(START.year, end.year + 1))
        log(f"{board} 除權息事件 {len(ev)} 筆")
        adj = adjust(panel, ev)
        trading = [d.date() for d in panel["close"].index]
        samples = month_ends(panel["close"].index, dt.date(2022, 6, 1))
        snaps = snapshots(board, tw, trading, samples)
        result[board] = {"raw": panel, "adj": adj, "names": names, "events": ev, "samples": samples, "snaps": snaps}
        log(f"{board} 完成：{len(names)} 檔、{len(trading)} 個交易日")

    def job_revenue():
        rev = {}
        y, m = START.year, START.month
        while dt.date(y, m, 1) < end.replace(day=1):
            for board in ("listed", "otc"):
                try:
                    for c, v in revenue_month(board, y, m).items():
                        rev[(c, y, m)] = v
                except Exception as e:  # noqa: BLE001
                    log(f"營收 {board} {y}/{m} 失敗：{e}")
            m += 1
            if m == 13:
                y, m = y + 1, 1
        result["revenue"] = rev
        log(f"月營收完成：{len(rev)} 筆")

    threads = [threading.Thread(target=job_market, args=("listed",)), threading.Thread(target=job_market, args=("otc",)),
               threading.Thread(target=job_revenue)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    with open(os.path.join(OUT, "dataset.pkl"), "wb") as f:
        pickle.dump(result, f)
    log(f"已寫入 {os.path.join(OUT, 'dataset.pkl')}")


def load():
    with open(os.path.join(OUT, "dataset.pkl"), "rb") as f:
        return pickle.load(f)


if __name__ == "__main__":
    build()
