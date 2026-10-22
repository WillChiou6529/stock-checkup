"""集保股權分散表：千張大戶、400 張以上大戶、散戶持股比例與股東人數（集保結算所，免費）。

- 最新一週全市場：opendata 1-5（一次一個檔案）
- 單一股票近一年歷史：集保查詢頁（表單 POST，需先取得 token），每週結果永久快取
"""
from __future__ import annotations

import http.cookiejar
import json
import os
import re
import threading
import time
import urllib.parse
import urllib.request

from .http import CACHE_DIR, UA, _cache_path, _throttle, get_text

QUERY_URL = "https://www.tdcc.com.tw/portal/zh/smWeb/qryStock"
ROW_RE = re.compile(r"<td[^>]*>\s*(\d+)\s*</td>\s*<td[^>]*>[^<]*</td>\s*<td[^>]*>\s*([\d,]+)\s*</td>\s*"
                    r"<td[^>]*>\s*([\d,]+)\s*</td>\s*<td[^>]*>\s*([\d.]+)\s*</td>")


def _summ(levels: dict) -> dict:
    """levels: {級距: (人數, 股數, 比例%)} -> 摘要。級距 15 = 1000 張以上，12~15 = 400 張以上，1~8 = 50 張以下。"""
    pct = lambda ks: round(sum(levels.get(k, (0, 0, 0))[2] for k in ks), 2)  # noqa: E731
    total = levels.get(17, (0, 0, 0))[0] or sum(v[0] for k, v in levels.items() if k <= 15)
    return {"big1000": pct([15]), "big400": pct([12, 13, 14, 15]), "retail50": pct(range(1, 9)),
            "big1000_n": levels.get(15, (0, 0, 0))[0], "holders": total}


class TDCC:
    def __init__(self):
        self._latest = None
        self._latest_time = 0.0
        self._lock = threading.Lock()
        self._cj = http.cookiejar.CookieJar()
        self._opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self._cj))
        self._token = None
        self._dates: list = []
        self._token_time = 0.0

    # ---- 全市場最新一週
    def latest(self) -> dict:
        with self._lock:
            if self._latest is not None and time.time() - self._latest_time < 6 * 3600:
                return self._latest
            text = get_text("https://opendata.tdcc.com.tw/getOD.ashx?id=1-5", ttl=6 * 3600, encoding="utf-8-sig")
            raw: dict = {}
            date = None
            for line in text.splitlines()[1:]:
                p = line.split(",")
                if len(p) < 6:
                    continue
                date = p[0]
                code = p[1].strip()  # 代號右側補空白到 6 碼
                try:
                    raw.setdefault(code, {})[int(p[2])] = (int(p[3]), int(p[4]), float(p[5]))
                except ValueError:
                    continue
            self._latest = {"date": date, "stocks": {c: _summ(v) for c, v in raw.items()}}
            self._latest_time = time.time()
            return self._latest

    # ---- 單一股票歷史
    def _refresh_token(self):
        if self._token and time.time() - self._token_time < 600:
            return
        _throttle(QUERY_URL)
        req = urllib.request.Request(QUERY_URL, headers={"User-Agent": UA})
        html = self._opener.open(req, timeout=30).read().decode("utf-8", "replace")
        m = re.search(r'name="SYNCHRONIZER_TOKEN" value="([^"]+)"', html)
        self._token = m.group(1) if m else None
        self._dates = re.findall(r'<option value="(\d{8})"', html)
        self._token_time = time.time()

    def dates(self) -> list:
        with self._lock:
            self._refresh_token()
            return list(self._dates)

    def week(self, code: str, date: str) -> dict | None:
        path = _cache_path(f"tdcc|{code}|{date}")
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        with self._lock:
            self._refresh_token()
            if not self._token:
                return None
            body = urllib.parse.urlencode({
                "SYNCHRONIZER_TOKEN": self._token, "SYNCHRONIZER_URI": "/portal/zh/smWeb/qryStock", "method": "submit",
                "firDate": date, "scaDate": date, "sqlMethod": "StockNo", "stockNo": code, "stockName": ""}).encode()
            _throttle(QUERY_URL)
            req = urllib.request.Request(QUERY_URL, data=body, headers={
                "User-Agent": UA, "Content-Type": "application/x-www-form-urlencoded", "Referer": QUERY_URL})
            try:
                html = self._opener.open(req, timeout=30).read().decode("utf-8", "replace")
            except Exception:  # noqa: BLE001
                self._token = None
                return None
            # 每次查詢後 token 會更新
            m = re.search(r'name="SYNCHRONIZER_TOKEN" value="([^"]+)"', html)
            if m:
                self._token = m.group(1)
        levels = {}
        for lv, n, sh, p in ROW_RE.findall(html):
            levels[int(lv)] = (int(n.replace(",", "")), int(sh.replace(",", "")), float(p))
        if not levels:
            return None
        out = _summ(levels)
        out["date"] = date
        os.makedirs(CACHE_DIR, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(out, f)
        return out

    def history(self, code: str, weeks: int = 8) -> list:
        """近 N 週（舊 -> 新）。"""
        out = []
        for d in self.dates()[:weeks]:
            w = self.week(code, d)
            if w:
                out.append(w)
        return list(reversed(out))


def describe(hist: list) -> dict:
    """把大戶歷史翻成重點與白話解讀（不計分，見 validate 結果再決定）。"""
    if not hist:
        return {}
    last, first = hist[-1], hist[0]
    d_big = last["big1000"] - first["big1000"]
    d_hold = (last["holders"] / first["holders"] - 1) if first["holders"] else 0
    notes = []
    if d_big > 1:
        notes.append((1, f"千張大戶持股 {len(hist)} 週增加 {d_big:+.2f} 個百分點（大戶在加碼）"))
    elif d_big < -1:
        notes.append((-1, f"千張大戶持股 {len(hist)} 週減少 {d_big:+.2f} 個百分點（大戶在減碼）"))
    if d_hold < -0.03:
        notes.append((1, f"股東人數減少 {d_hold:+.1%}（籌碼往少數人集中）"))
    elif d_hold > 0.05:
        notes.append((-1, f"股東人數增加 {d_hold:+.1%}（籌碼分散到散戶手上）"))
    return {"weeks": hist, "change_big1000": d_big, "change_holders": d_hold, "notes": notes}
