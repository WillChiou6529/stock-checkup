"""造訪紀錄：裝置辨識 + 儲存（有 DATABASE_URL 用 PostgreSQL，否則用本機 SQLite）。

Render 免費主機的硬碟在休眠/重新部署後會清空，所以正式環境請設 DATABASE_URL（例如 Neon 免費 Postgres），
紀錄才會永久保存。資料表名稱加 stock_ 前綴，可以和其他專案共用同一個資料庫。
"""
from __future__ import annotations

import datetime as dt
import os
import re
import sqlite3
import threading
from collections import Counter

TW = dt.timezone(dt.timedelta(hours=8))
COLS = ("ts", "visitor", "ip", "ua", "os", "os_ver", "device", "model", "browser", "path", "screen", "lang", "referrer")

# 依「螢幕 CSS 寬 x 高 @ 像素比」推測 iPhone 機型（Safari 不提供型號）
IPHONE_SCREENS = {
    (375, 667, 2): "iPhone SE / 6 / 7 / 8",
    (414, 736, 3): "iPhone 6/7/8 Plus",
    (375, 812, 3): "iPhone X / XS / 11 Pro / 12 mini / 13 mini",
    (414, 896, 2): "iPhone XR / 11",
    (414, 896, 3): "iPhone XS Max / 11 Pro Max",
    (390, 844, 3): "iPhone 12 / 13 / 14",
    (428, 926, 3): "iPhone 12/13 Pro Max / 14 Plus",
    (393, 852, 3): "iPhone 14 Pro / 15 / 15 Pro / 16",
    (430, 932, 3): "iPhone 14 Pro Max / 15 Plus / 15 Pro Max / 16 Plus",
    (402, 874, 3): "iPhone 16 Pro",
    (440, 956, 3): "iPhone 16 Pro Max",
}
# Android 型號代碼前綴 -> 品牌
ANDROID_BRANDS = [
    (r"SM-|SC-|SCV|GT-", "Samsung"), (r"Pixel", "Google"), (r"CPH|PH[A-Z]\d", "OPPO"), (r"RMX", "realme"),
    (r"V2\d{3}|vivo", "vivo"), (r"M2\d{3}|2\d{3}[A-Z0-9]{3,}|Redmi|POCO|MI ", "Xiaomi"), (r"ASUS|ASUS_", "ASUS"),
    (r"XQ-|SO-", "Sony"), (r"moto|XT\d", "Motorola"), (r"Nokia|TA-", "Nokia"), (r"HTC", "HTC"),
    (r"A\d{3}|NE\d{4}|CPH\d+|LE\d{4}|KB\d{4}|IN\d{4}|HD\d{4}|GM\d{4}", "OnePlus"), (r"Nothing|A06", "Nothing"),
]
BOT_RE = re.compile(r"bot|crawl|spider|slurp|headless|curl|python|wget|httpclient|go-http|render|uptime|monitor", re.I)


def parse_ua(ua: str, info: dict | None = None) -> dict:
    """把 User-Agent（加上前端回報的 Client Hints / 螢幕資訊）翻成人看得懂的裝置資料。"""
    u, info = ua or "", info or {}
    ch = info.get("ch") or {}
    os_, ver, device, model = "其他", "", "電腦", ""

    if "iPhone" in u:
        os_, device = "iPhone", "手機"
        m = re.search(r"OS (\d+(?:_\d+)*)", u)
        ver = "iOS " + m.group(1).replace("_", ".") if m else "iOS"
        try:
            key = (int(info.get("sw", 0)), int(info.get("sh", 0)), int(round(float(info.get("dpr", 0)))))
            key = (min(key[0], key[1]), max(key[0], key[1]), key[2])
            model = IPHONE_SCREENS.get(key, "iPhone") + ("（依螢幕推測）" if key in IPHONE_SCREENS else "")
        except (TypeError, ValueError):
            model = "iPhone"
    elif "iPad" in u or ("Macintosh" in u and int(info.get("touch") or 0) > 1):
        os_, device, model = "iPad", "平板", "iPad"
        m = re.search(r"OS (\d+(?:_\d+)*)", u)
        ver = "iPadOS " + m.group(1).replace("_", ".") if m else "iPadOS"
    elif "Android" in u:
        os_ = "Android"
        m = re.search(r"Android ([\d.]+)", u)
        ver = "Android " + m.group(1) if m else "Android"
        if ch.get("platformVersion"):
            ver = "Android " + str(ch["platformVersion"]).split(".")[0]
        device = "手機" if ("Mobile" in u or ch.get("mobile")) else "平板"
        model = (ch.get("model") or "").strip()
        if not model:
            m = re.search(r"Android [\d.]+;\s*(?:[a-z]{2}[-_][A-Za-z]{2};\s*)?([^;)]+?)(?:\s+Build/|\))", u)
            model = m.group(1).strip() if m else ""
        if model in ("K", "k", ""):  # Chrome 精簡版 UA 會把型號藏成 K
            model = "Android 裝置"
        else:
            brand = next((b for pat, b in ANDROID_BRANDS if re.match(pat, model, re.I)), "")
            if brand and not model.lower().startswith(brand.lower()):
                model = f"{brand} {model}"
    elif "Windows NT" in u:
        os_ = "Windows"
        pv = str(ch.get("platformVersion") or "")
        major = int(pv.split(".")[0]) if pv.split(".")[0].isdigit() else 0
        ver = "Windows 11" if major >= 13 else "Windows 10" if "Windows NT 10" in u else "Windows"
    elif "Mac OS X" in u:
        os_ = "Mac"
        m = re.search(r"Mac OS X (\d+(?:[_.]\d+)*)", u)
        ver = "macOS " + m.group(1).replace("_", ".") if m else "macOS"
    elif "CrOS" in u:
        os_, ver = "ChromeOS", "ChromeOS"
    elif "Linux" in u:
        os_, ver = "Linux", "Linux"

    browsers = [(r"\bLine/", "LINE 內建"), (r"FBAN|FBAV", "Facebook 內建"), (r"Instagram", "Instagram 內建"),
                (r"SamsungBrowser", "Samsung 瀏覽器"), (r"EdgA|EdgiOS|Edg/", "Edge"), (r"OPR/|Opera", "Opera"),
                (r"Firefox|FxiOS", "Firefox"), (r"CriOS|Chrome/", "Chrome"), (r"Safari", "Safari")]
    browser = next((name for pat, name in browsers if re.search(pat, u)), "其他")
    if BOT_RE.search(u):
        device, os_ = "機器人", "機器人"
    return {"os": os_, "os_ver": ver, "device": device, "model": model[:60], "browser": browser}


def mask_ip(ip: str) -> str:
    """只保留 IP 前兩段（例如 114.32.x.x），足以分辨地區/電信商，不會精準到個人。"""
    ip = (ip or "").strip()
    if ":" in ip:  # IPv6
        parts = ip.split(":")
        return ":".join(parts[:3]) + ":…" if len(parts) > 3 else ip
    parts = ip.split(".")
    return ".".join(parts[:2]) + ".x.x" if len(parts) == 4 else ip


class Store:
    def __init__(self, sqlite_path: str):
        self.url = os.environ.get("DATABASE_URL", "").strip()
        self.kind = "postgres" if self.url else "sqlite"
        self.sqlite_path = sqlite_path
        self._lock = threading.Lock()
        self._conn = None
        self.error = None
        try:
            self._init()
        except Exception as e:  # noqa: BLE001
            self.error = f"資料庫初始化失敗：{e}"

    # ---- 連線
    def _connect(self):
        if self._conn is not None:
            return self._conn
        if self.kind == "postgres":
            import psycopg2  # 延遲載入：本機沒設 DATABASE_URL 時不需要
            self._conn = psycopg2.connect(self.url, connect_timeout=10)
            self._conn.autocommit = True
        else:
            os.makedirs(os.path.dirname(self.sqlite_path), exist_ok=True)
            self._conn = sqlite3.connect(self.sqlite_path, check_same_thread=False)
            self._conn.isolation_level = None
        return self._conn

    def _exec(self, sql: str, params=(), fetch=False):
        if self.kind == "sqlite":
            sql = sql.replace("%s", "?")
        with self._lock:
            for attempt in range(2):
                try:
                    cur = self._connect().cursor()
                    cur.execute(sql, params)
                    return cur.fetchall() if fetch else None
                except Exception:  # noqa: BLE001
                    # Neon 閒置會斷線，重連一次再試
                    try:
                        self._conn.close()
                    except Exception:  # noqa: BLE001
                        pass
                    self._conn = None
                    if attempt:
                        raise

    def _init(self):
        pk = "SERIAL PRIMARY KEY" if self.kind == "postgres" else "INTEGER PRIMARY KEY AUTOINCREMENT"
        cols = ", ".join(f"{c} TEXT" for c in COLS)
        self._exec(f"CREATE TABLE IF NOT EXISTS stock_visits (id {pk}, {cols})")
        self._exec("CREATE INDEX IF NOT EXISTS stock_visits_ts ON stock_visits (ts)")

    # ---- 寫入 / 讀取
    def add(self, row: dict):
        vals = tuple(str(row.get(c, "") or "")[:500] for c in COLS)
        self._exec(f"INSERT INTO stock_visits ({', '.join(COLS)}) VALUES ({', '.join(['%s'] * len(COLS))})", vals)

    def add_async(self, row: dict):
        def run():
            try:
                self.add(row)
                self.error = None
            except Exception as e:  # noqa: BLE001
                self.error = f"寫入失敗：{e}"
        threading.Thread(target=run, daemon=True).start()

    def recent(self, limit: int = 5000):
        rows = self._exec(f"SELECT id, {', '.join(COLS)} FROM stock_visits ORDER BY id DESC LIMIT %s", (limit,), fetch=True)
        return [dict(zip(("id",) + COLS, r)) for r in rows or []]


def to_local(ts: str) -> dt.datetime:
    d = dt.datetime.fromisoformat(ts)
    if d.tzinfo is None:
        d = d.replace(tzinfo=dt.timezone.utc)
    return d.astimezone(TW)


def summarize(rows: list, include_bots: bool, days: int = 14) -> dict:
    """後台統計：總數、不重複訪客、今日、各系統/裝置/瀏覽器/型號排行、每日走勢。"""
    rs = [r for r in rows if include_bots or r["device"] != "機器人"]
    now = dt.datetime.now(TW)
    today = now.date()
    for r in rs:
        r["_local"] = to_local(r["ts"])
    day_counts = Counter(r["_local"].date() for r in rs)
    daily = []
    for i in range(days - 1, -1, -1):
        d = today - dt.timedelta(days=i)
        daily.append({"date": d.isoformat(), "visits": day_counts.get(d, 0),
                      "visitors": len({r["visitor"] for r in rs if r["_local"].date() == d})})
    top = lambda key, n=8: Counter(r[key] or "未知" for r in rs).most_common(n)  # noqa: E731
    models = Counter(r["model"] for r in rs if r["model"]).most_common(10)
    last7 = [r for r in rs if (now - r["_local"]).days < 7]
    return {
        "total": len(rs), "visitors": len({r["visitor"] for r in rs}),
        "today": day_counts.get(today, 0), "today_visitors": len({r["visitor"] for r in rs if r["_local"].date() == today}),
        "week": len(last7), "mobile_share": (sum(1 for r in rs if r["device"] in ("手機", "平板")) / len(rs)) if rs else 0,
        "os": top("os"), "device": top("device"), "browser": top("browser"), "models": models,
        "pages": Counter(_page_name(r["path"]) for r in rs).most_common(8), "daily": daily,
        "first": rs[-1]["_local"].strftime("%Y-%m-%d %H:%M") if rs else None,
    }


def _page_name(path: str) -> str:
    p = (path or "").lstrip("#")
    if p.startswith("/stock/"):
        return "個股 " + p.split("/")[2]
    return {"/": "首頁", "": "首頁", "/scan": "今日選股", "/about": "怎麼看懂"}.get(p, p or "首頁")
