"""資料來源：Yahoo 價格 + 證交所/櫃買中心官方 OpenAPI（基本面、籌碼）。"""
from __future__ import annotations

import datetime as dt
import functools
import threading
import urllib.parse

import numpy as np
import pandas as pd

from .http import get_json

YAHOO_CHART = "https://query1.finance.yahoo.com/v8/finance/chart/{sym}?range={rng}&interval=1d&events=div%2Csplit"


def num(x):
    """'1,234.5' / '-' / '' -> float 或 None。"""
    if x is None:
        return None
    if isinstance(x, (int, float)):
        return float(x)
    s = str(x).replace(",", "").strip()
    if s in ("", "-", "--", "---", "N/A", "X"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _pick(row: dict, *needles):
    """中文欄位名用子字串找，避免 API 欄位小幅改名就壞掉。"""
    for k, v in row.items():
        if all(n in k for n in needles):
            return v
    return None


# ---------------------------------------------------------------- Yahoo
def yahoo_history(symbol: str, rng: str = "5y"):
    """回傳 (DataFrame, meta)。OHLC 已用還原權息價調整，raw_close 為實際收盤價。"""
    url = YAHOO_CHART.format(sym=urllib.parse.quote(symbol), rng=rng)
    data = get_json(url, ttl=900)
    res = (data.get("chart") or {}).get("result")
    if not res:
        raise RuntimeError(f"Yahoo 查無 {symbol}")
    r = res[0]
    meta = r.get("meta", {})
    ts = r.get("timestamp") or []
    q = r["indicators"]["quote"][0]
    adj = (r["indicators"].get("adjclose") or [{}])[0].get("adjclose")
    off = meta.get("gmtoffset", 0) or 0
    idx = pd.to_datetime(np.array(ts, dtype="int64") + off, unit="s").normalize()
    df = pd.DataFrame({
        "open": q.get("open"), "high": q.get("high"), "low": q.get("low"),
        "close": q.get("close"), "volume": q.get("volume"),
    }, index=idx, dtype="float64")
    df["adjclose"] = pd.Series(adj, index=idx, dtype="float64") if adj else df["close"]
    df = df[~df.index.duplicated(keep="last")].dropna(subset=["close"])
    df["volume"] = df["volume"].fillna(0)
    df["adjclose"] = df["adjclose"].fillna(df["close"])
    f = df["adjclose"] / df["close"]
    df["raw_close"] = df["close"]
    for c in ("open", "high", "low"):
        df[c] = (df[c].fillna(df["close"]) * f)
    df["close"] = df["adjclose"]
    df = df.drop(columns="adjclose")
    # 盤中執行時 Yahoo 最後一根是即時價，以 meta 為準
    if meta.get("regularMarketPrice") and len(df):
        meta["last_price"] = meta["regularMarketPrice"]
    else:
        meta["last_price"] = float(df["raw_close"].iloc[-1]) if len(df) else None
    return df, meta


# ---------------------------------------------------------------- 台股
def _roc_to_date(s: str):
    s = str(s).strip()
    try:
        if len(s) == 7:      # 1151019
            return dt.date(int(s[:3]) + 1911, int(s[3:5]), int(s[5:7]))
        if len(s) == 8:      # 20261019
            return dt.date(int(s[:4]), int(s[4:6]), int(s[6:8]))
    except ValueError:
        pass
    return None


def _settled(d: dt.date) -> bool:
    """兩天前以前的日期資料已定案：查無資料就是休市，可以永久快取空結果。"""
    return d <= dt.date.today() - dt.timedelta(days=2)


def _ok_twse(d):
    return isinstance(d, dict) and d.get("stat") == "OK" and (d.get("data") or d.get("tables"))


def _ok_tpex(d):
    try:
        return bool(d["tables"][0]["data"])
    except (KeyError, IndexError, TypeError):
        return False


def _locked(fn):
    @functools.wraps(fn)
    def wrap(self, *a, **k):
        with self._lock:
            return fn(self, *a, **k)
    return wrap


class TWMarket:
    """台股官方資料，延遲載入、整個執行期間共用（執行緒安全，可供網站使用）。"""

    def __init__(self, trading_dates: list):
        # trading_dates: 由大盤指數歷史取得的交易日（舊->新）
        # Yahoo 的指數歷史偶爾會漏掉某天，所以再補上最近 40 天的所有平日；
        # 假日查官方資料會回空結果，會被略過（並快取，見 _settled）
        dates = {d.date() if hasattr(d, "date") else d for d in trading_dates}
        today = dt.date.today()
        for i in range(41):
            d = today - dt.timedelta(days=i)
            if d.weekday() < 5:
                dates.add(d)
        self.trading_dates = sorted(d for d in dates if d <= today)
        self._listed = None
        self._otc = None
        self._t86 = {}
        self._tpex_inst = {}
        self._margin = {}
        self._memo = {}
        self._lock = threading.RLock()
        self.warnings: list = []

    @_locked
    def _json(self, url, ttl):
        """同一個 Context 內重複用到的大型清單只讀一次。"""
        if url not in self._memo:
            self._memo[url] = get_json(url, ttl=ttl)
        return self._memo[url]

    def names(self) -> dict:
        """{代號: (名稱, 'listed'/'otc')}，供網站搜尋用。"""
        self._load_universe()
        out = {c: (v["name"], "otc") for c, v in self._otc.items()}
        out.update({c: (v["name"], "listed") for c, v in self._listed.items()})
        return out

    # ---- 股票清單 / 估值 / 基本面 -------------------------------------
    @_locked
    def _load_universe(self):
        if self._listed is not None:
            return
        self._listed, self._otc = {}, {}
        try:
            for r in self._json("https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL", 3600):
                self._listed[r["Code"].strip()] = {
                    "name": r["Name"].strip(), "close": num(r.get("ClosingPrice")),
                    "value": num(r.get("TradeValue")), "volume": num(r.get("TradeVolume")),
                    "change": num(r.get("Change")), "date": _roc_to_date(r.get("Date", "")),
                }
        except Exception as e:  # noqa: BLE001
            self.warnings.append(f"上市行情載入失敗：{e}")
        try:
            for r in self._json("https://www.tpex.org.tw/openapi/v1/tpex_mainboard_daily_close_quotes", 3600):
                code = r["SecuritiesCompanyCode"].strip()
                self._otc[code] = {
                    "name": r["CompanyName"].strip(), "close": num(r.get("Close")),
                    "value": num(r.get("TransactionAmount")), "volume": num(r.get("TradingShares")),
                    "change": num(r.get("Change")), "date": _roc_to_date(r.get("Date", "")),
                }
        except Exception as e:  # noqa: BLE001
            self.warnings.append(f"上櫃行情載入失敗：{e}")

    def resolve(self, code: str):
        """'2330' -> ('2330.TW', '台積電', 'listed')；查不到回 None。"""
        self._load_universe()
        if code in self._listed:
            return f"{code}.TW", self._listed[code]["name"], "listed"
        if code in self._otc:
            return f"{code}.TWO", self._otc[code]["name"], "otc"
        return None

    def valuation(self, code: str, board: str) -> dict:
        try:
            if board == "listed":
                rows = self._json("https://openapi.twse.com.tw/v1/exchangeReport/BWIBBU_ALL", 3600)
                for r in rows:
                    if r.get("Code", "").strip() == code:
                        return {"pe": num(r.get("PEratio")), "yield": num(r.get("DividendYield")),
                                "pb": num(r.get("PBratio"))}
            else:
                rows = self._json("https://www.tpex.org.tw/openapi/v1/tpex_mainboard_peratio_analysis", 3600)
                for r in rows:
                    if r.get("SecuritiesCompanyCode", "").strip() == code:
                        return {"pe": num(r.get("PriceEarningRatio")), "yield": num(r.get("YieldRatio")),
                                "pb": num(r.get("PriceBookRatio"))}
        except Exception as e:  # noqa: BLE001
            self.warnings.append(f"{code} 估值資料失敗：{e}")
        return {}

    def _revenue_rows(self, board: str):
        url = ("https://openapi.twse.com.tw/v1/opendata/t187ap05_L" if board == "listed"
               else "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap05_O")
        return self._json(url, 6 * 3600)

    def revenue(self, code: str, board: str) -> dict:
        try:
            for r in self._revenue_rows(board):
                if str(_pick(r, "公司代號") or "").strip() == code:
                    ym = str(_pick(r, "資料年月") or "")
                    return {
                        "period": f"{int(ym[:3]) + 1911}/{ym[3:]}" if len(ym) == 5 else ym,
                        "rev": num(_pick(r, "營業收入", "當月營收")),
                        "mom": num(_pick(r, "上月比較增減")),
                        "yoy": num(_pick(r, "去年同月增減")),
                        "cum_yoy": num(_pick(r, "前期比較增減")),
                        "industry": _pick(r, "產業別"),
                        "note": (_pick(r, "備註") or "").strip(" -"),
                    }
        except Exception as e:  # noqa: BLE001
            self.warnings.append(f"{code} 月營收資料失敗：{e}")
        return {}

    def industry_peers(self, code: str):
        """(產業別, [(代號, 名稱, Yahoo 代號, 成交金額)])：依證交所/櫃買產業分類找同業（只含一般股票）。"""
        self._load_universe()
        ind_of, members = {}, {}
        for board in ("listed", "otc"):
            try:
                rows = self._revenue_rows(board)
            except Exception:  # noqa: BLE001
                continue
            for r in rows:
                c = str(_pick(r, "公司代號") or "").strip()
                ind = str(_pick(r, "產業別") or "").strip()
                if c and ind:
                    ind_of[c] = ind
                    members.setdefault(ind, []).append((c, board))
        ind = ind_of.get(code)
        if not ind:
            return None, []
        out = []
        for c, board in members[ind]:
            q = (self._listed if board == "listed" else self._otc).get(c)
            if q:
                out.append((c, q["name"], f"{c}.TW" if board == "listed" else f"{c}.TWO", q["value"] or 0))
        return ind, out

    def eps(self, code: str, board: str) -> dict:
        url = ("https://openapi.twse.com.tw/v1/opendata/t187ap14_L" if board == "listed"
               else "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap14_O")
        try:
            for r in self._json(url, 6 * 3600):
                c = r.get("公司代號") or r.get("SecuritiesCompanyCode") or _pick(r, "代號")
                if str(c or "").strip() == code:
                    year = r.get("年度") or r.get("Year")
                    q = num(_pick(r, "季別"))
                    return {"year": int(num(year)) + 1911 if num(year) else None,
                            "quarter": int(q) if q else None,
                            "eps_ytd": num(_pick(r, "基本每股盈餘"))}
        except Exception as e:  # noqa: BLE001
            self.warnings.append(f"{code} EPS 資料失敗：{e}")
        return {}

    # ---- 籌碼 -------------------------------------------------------
    @_locked
    def _t86_day(self, d: dt.date):
        if d in self._t86:
            return self._t86[d]
        url = f"https://www.twse.com.tw/rwd/zh/fund/T86?date={d:%Y%m%d}&selectType=ALLBUT0999&response=json"
        out = None
        try:
            js = get_json(url, ttl=None, keep=lambda j: _ok_twse(j) or _settled(d))
            if _ok_twse(js):
                # 0代號 4外資買賣超 10投信 11自營商 18三大法人（單位：股）
                out = {row[0].strip(): (num(row[4]), num(row[10]), num(row[11]), num(row[18]))
                       for row in js["data"]}
        except Exception as e:  # noqa: BLE001
            self.warnings.append(f"上市法人 {d} 失敗：{e}")
        self._t86[d] = out
        return out

    @_locked
    def _tpex_inst_day(self, d: dt.date):
        if d in self._tpex_inst:
            return self._tpex_inst[d]
        url = ("https://www.tpex.org.tw/www/zh-tw/insti/dailyTrade?type=Daily&sect=EW&date="
               + urllib.parse.quote(f"{d:%Y/%m/%d}") + "&response=json")
        out = None
        try:
            js = get_json(url, ttl=None, keep=lambda j: _ok_tpex(j) or _settled(d))
            if _ok_tpex(js):
                # 0代號 4外資(不含自營) 13投信 22自營商合計 23三大法人
                out = {row[0].strip(): (num(row[4]), num(row[13]), num(row[22]), num(row[23]))
                       for row in js["tables"][0]["data"]}
        except Exception as e:  # noqa: BLE001
            self.warnings.append(f"上櫃法人 {d} 失敗：{e}")
        self._tpex_inst[d] = out
        return out

    def recent_inst_days(self, board: str, n: int):
        """最近 n 個「已公布」法人資料的交易日（新->舊）。"""
        loader = self._t86_day if board == "listed" else self._tpex_inst_day
        days = []
        for d in reversed(self.trading_dates[-(n + 6):]):
            if loader(d) is not None:
                days.append(d)
            if len(days) >= n:
                break
        return days

    def institutional(self, code: str, board: str, n: int = 10) -> pd.DataFrame:
        """每日三大法人買賣超（張），舊->新。"""
        loader = self._t86_day if board == "listed" else self._tpex_inst_day
        rows = []
        for d in reversed(self.recent_inst_days(board, n)):
            day = loader(d) or {}
            v = day.get(code)
            if v is None:
                v = (0.0, 0.0, 0.0, 0.0)  # 當天無法人進出
            rows.append({"date": d, "foreign": (v[0] or 0) / 1000, "trust": (v[1] or 0) / 1000,
                         "dealer": (v[2] or 0) / 1000, "total": (v[3] or 0) / 1000})
        return pd.DataFrame(rows)

    @_locked
    def _margin_day(self, d: dt.date, board: str):
        key = (board, d)
        if key in self._margin:
            return self._margin[key]
        out = None
        try:
            if board == "listed":
                url = f"https://www.twse.com.tw/rwd/zh/marginTrading/MI_MARGN?date={d:%Y%m%d}&selectType=ALL&response=json"
                js = get_json(url, ttl=None, keep=lambda j: (_ok_twse(j) and len(j.get("tables", [])) > 1
                                                        and bool(j["tables"][1].get("data"))) or _settled(d))
                tables = [t for t in js.get("tables", []) if t.get("data")] if _ok_twse(js) else []
                if tables:
                    t = max(tables, key=lambda t: len(t["data"]))
                    # 6融資今日餘額 12融券今日餘額（張）
                    out = {r[0].strip(): (num(r[6]), num(r[12])) for r in t["data"] if len(r) > 12}
            else:
                url = ("https://www.tpex.org.tw/www/zh-tw/margin/balance?date="
                       + urllib.parse.quote(f"{d:%Y/%m/%d}") + "&response=json")
                js = get_json(url, ttl=None, keep=lambda j: _ok_tpex(j) or _settled(d))
                if _ok_tpex(js):
                    # 6資餘額 14券餘額（張）
                    out = {r[0].strip(): (num(r[6]), num(r[14])) for r in js["tables"][0]["data"] if len(r) > 14}
        except Exception as e:  # noqa: BLE001
            self.warnings.append(f"融資融券 {d} 失敗：{e}")
        self._margin[key] = out
        return out

    def margin(self, code: str, board: str, lookback: int = 10) -> dict:
        """最新融資/融券餘額與 lookback 個交易日前比較。"""
        latest = None
        dates = list(reversed(self.trading_dates[-(lookback + 8):]))
        i_latest = None
        for i, d in enumerate(dates[:5]):
            m = self._margin_day(d, board)
            if m is not None:
                latest, i_latest = (d, m), i
                break
        if latest is None:
            return {}
        past = None
        for d in dates[i_latest + lookback:i_latest + lookback + 3]:
            m = self._margin_day(d, board)
            if m is not None:
                past = (d, m)
                break
        cur = latest[1].get(code)
        if not cur:
            return {}
        out = {"date": latest[0], "margin": cur[0], "short": cur[1]}
        if past and past[1].get(code):
            out.update({"past_date": past[0], "margin_past": past[1][code][0], "short_past": past[1][code][1]})
        return out

    def market_flows(self) -> dict:
        """上市三大法人買賣金額（億元），最新一個有資料的交易日。"""
        for d in reversed(self.trading_dates[-5:]):
            url = f"https://www.twse.com.tw/rwd/zh/fund/BFI82U?dayDate={d:%Y%m%d}&type=day&response=json"
            try:
                js = get_json(url, ttl=None, keep=lambda j: _ok_twse(j) or _settled(d))
            except Exception:  # noqa: BLE001
                continue
            if not _ok_twse(js):
                continue
            out = {"date": d}
            for row in js["data"]:
                name, diff = row[0], (num(row[3]) or 0) / 1e8
                if name.startswith("外資及陸資"):
                    out["foreign"] = diff
                elif name.startswith("投信"):
                    out["trust"] = diff
                elif name.startswith("自營商"):
                    out["dealer"] = out.get("dealer", 0) + diff
                elif name.startswith("合計"):
                    out["total"] = diff
            return out
        return {}

    # ---- 選股掃描 ---------------------------------------------------
    def scan_candidates(self, top_n: int = 8, min_value_million: float = 100, days: int = 5) -> list:
        """用官方全市場資料粗篩：法人連續買超 + 營收成長 + 估值合理 + 有量。

        回傳 [(code, 說明)]，之後再交給完整分析。
        """
        self._load_universe()
        rev = {}
        for board in ("listed", "otc"):
            try:
                for r in self._revenue_rows(board):
                    c = str(_pick(r, "公司代號") or "").strip()
                    rev[c] = (num(_pick(r, "去年同月增減")), num(_pick(r, "前期比較增減")))
            except Exception:  # noqa: BLE001
                pass
        pe = {}
        try:
            for r in self._json("https://openapi.twse.com.tw/v1/exchangeReport/BWIBBU_ALL", 3600):
                pe[r["Code"].strip()] = num(r.get("PEratio"))
            for r in self._json("https://www.tpex.org.tw/openapi/v1/tpex_mainboard_peratio_analysis", 3600):
                pe[r["SecuritiesCompanyCode"].strip()] = num(r.get("PriceEarningRatio"))
        except Exception:  # noqa: BLE001
            pass

        results = []
        for board, quotes, loader in (("listed", self._listed, self._t86_day),
                                      ("otc", self._otc, self._tpex_inst_day)):
            ds = self.recent_inst_days(board, days)
            if not ds:
                continue
            agg, buy_days = {}, {}
            for d in ds:
                for code, v in (loader(d) or {}).items():
                    f, t = (v[0] or 0), (v[1] or 0)
                    a = agg.setdefault(code, [0.0, 0.0])
                    a[0] += f
                    a[1] += t
                    if f + t > 0:
                        buy_days[code] = buy_days.get(code, 0) + 1
            for code, (f, t) in agg.items():
                q = quotes.get(code)
                if not q or not (len(code) == 4 and code.isdigit()) or code.startswith("0"):
                    continue
                if not q["value"] or q["value"] < min_value_million * 1e6 or not q["volume"]:
                    continue
                yoy, cum = rev.get(code, (None, None))
                p = pe.get(code)
                if yoy is None or cum is None or yoy < 10 or cum < 5:
                    continue
                if p is None or p <= 0 or p > 35:
                    continue
                if buy_days.get(code, 0) < max(3, days - 2) or t < 0:
                    continue
                # 5 日法人買超占「今日成交量 x5」比重，粗估籌碼集中度
                ratio = (f + t) / (q["volume"] * days)
                if ratio <= 0.02:
                    continue
                score = ratio * 100 + min(yoy, 80) / 10 + (5 if t > 0 else 0) - max(p - 20, 0) / 3
                results.append((score, code, f"{days}日外資+投信買超 {(f + t) / 1000:,.0f} 張"
                                             f"（約成交量 {ratio * 100:.0f}%），營收年增 {yoy:.0f}%，本益比 {p:.1f}"))
        results.sort(reverse=True)
        return [(c, why) for _, c, why in results[:top_n]]
