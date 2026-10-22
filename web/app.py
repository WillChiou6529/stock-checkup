"""股票健診 網站後端（FastAPI）。

啟動：uvicorn web.app:app --port 8000
- 開機後背景「暖機」：先抓大盤、近 10 日三大法人、融資融券、估值/營收清單，之後查個股只需抓價格。
- 資料每 20 分鐘在背景更新一次；個股分析結果快取 10 分鐘。
"""
from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import json
import math
import os
import secrets
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pandas as pd
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from stock_analyzer.analyzer import Context, analyze, is_tw, market_overview
from stock_analyzer.http import CACHE_DIR

from .visits import Store, mask_ip, parse_ua, summarize, to_local

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATIC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
with open(os.path.join(ROOT, "config.json"), encoding="utf-8") as f:
    CFG = json.load(f)
CFG["holdings"] = {}  # 網站的持股由使用者在瀏覽器自行輸入

CTX_TTL = 20 * 60
STOCK_TTL = 10 * 60
SCAN_TTL = 30 * 60
CHART_DAYS = 500


# ------------------------------------------------------------------ JSON 轉換
def clean(o):
    """把 numpy / pandas / NaN / 日期 轉成可 JSON 序列化的型別。"""
    if isinstance(o, dict):
        return {str(k): clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [clean(v) for v in o]
    if isinstance(o, (np.floating, float)):
        v = float(o)
        return None if math.isnan(v) or math.isinf(v) else round(v, 4)
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.bool_,)):
        return bool(o)
    if isinstance(o, (pd.Timestamp, dt.datetime, dt.date)):
        return o.strftime("%Y-%m-%d")
    if isinstance(o, pd.DataFrame):
        return clean(o.to_dict(orient="records"))
    if isinstance(o, pd.Series):
        return clean(o.tolist())
    return o


def _col(df, c):
    return clean(df[c].tolist()) if c in df else []


def serialize(a: dict) -> dict:
    df, L = a["df"], a["last"]
    chart = {"t": [d.strftime("%Y-%m-%d") for d in df.index]}
    for k, c in (("o", "open"), ("h", "high"), ("l", "low"), ("c", "close"), ("v", "volume"),
                 ("ma20", "ma20"), ("ma60", "ma60"), ("ma120", "ma120"), ("ma240", "ma240"),
                 ("bbu", "bb_up"), ("bbl", "bb_lo"), ("rsi", "rsi"), ("k", "k"), ("d", "d"),
                 ("dif", "dif"), ("dea", "dea"), ("hist", "macd_hist")):
        chart[k] = _col(df, c)
    chart["tech"] = clean(a["tech_hist"].tolist())
    ind = {k: L.get(k) for k in ("rsi", "k", "d", "macd_hist", "dif", "adx", "bias20", "vol60", "dd252", "ret5",
                                  "ret20", "ret60", "ret240", "mom12_1", "rs60", "ma5", "ma20", "ma60", "ma120",
                                  "ma240", "hi252", "lo252", "vol_ratio", "atr", "chandelier")}
    out = {k: a[k] for k in ("code", "name", "symbol", "board", "tw", "etf", "note", "price", "date", "chg", "currency",
                             "score", "tech_score", "scores", "notes", "rating", "action", "caveats", "summary",
                             "plan", "backtest", "bt_verdict", "valuation", "revenue", "eps", "inst", "margin")}
    out.update({"chart": chart, "ind": ind, "spark": clean(df["raw_close"].tail(60).tolist())})
    return clean(out)


def summary_row(s: dict) -> dict:
    row = {k: s.get(k) for k in ("code", "name", "board", "tw", "etf", "price", "chg", "score", "rating", "action",
                                 "scores", "note", "spark", "currency")}
    row.update({"rr": s["plan"].get("rr"), "stop": s["plan"].get("stop"), "target1": s["plan"].get("target1"),
                "entry": s["plan"].get("entry")})
    return row


# ------------------------------------------------------------------ 引擎
class Engine:
    def __init__(self):
        self._ctx = None
        self._ctx_time = 0.0
        self._ctx_lock = threading.Lock()
        self._refreshing = False
        self._cache: dict = {}
        self._cache_lock = threading.Lock()
        self._key_locks: dict = {}
        self.status = {"ready": False, "stage": "啟動中", "pct": 0, "error": None}
        self.pool = ThreadPoolExecutor(max_workers=4)

    # ---- Context（大盤、交易日、台股官方資料）
    def _build(self):
        c = Context(CFG)
        c.market = market_overview(c, True)
        return c

    def ctx(self) -> Context:
        if self._ctx is None:
            with self._ctx_lock:
                if self._ctx is None:
                    self._ctx, self._ctx_time = self._build(), time.time()
        elif time.time() - self._ctx_time > CTX_TTL and not self._refreshing:
            self._refreshing = True
            threading.Thread(target=self._refresh, daemon=True).start()
        return self._ctx

    def _refresh(self):
        try:
            c = self._build()
            self._ctx, self._ctx_time = c, time.time()
            self._prefetch(c)
            with self._cache_lock:
                self._cache.pop("scan", None)
        except Exception as e:  # noqa: BLE001
            self.status["error"] = f"背景更新失敗：{e}"
        finally:
            self._refreshing = False

    def _prefetch(self, c: Context):
        tw = c.tw
        n = int(CFG.get("chip_lookback_days", 10))
        tw.names()
        tw.valuation("2330", "listed"), tw.valuation("6488", "otc")
        tw.revenue("2330", "listed"), tw.revenue("6488", "otc")
        tw.eps("2330", "listed"), tw.eps("6488", "otc")
        self.status.update(stage="下載上市三大法人", pct=35)
        tw.recent_inst_days("listed", n)
        self.status.update(stage="下載上櫃三大法人", pct=55)
        tw.recent_inst_days("otc", n)
        self.status.update(stage="下載融資融券", pct=75)
        tw.margin("2330", "listed", n), tw.margin("6488", "otc", n)

    def warm(self):
        try:
            self.status.update(stage="下載大盤資料", pct=10)
            c = self.ctx()
            self.status.update(stage="下載估值與營收", pct=20)
            self._prefetch(c)
            self.status.update(stage="全市場選股掃描", pct=90)
            self.scan()
            self.status.update(ready=True, stage="完成", pct=100)
        except Exception as e:  # noqa: BLE001
            self.status.update(ready=True, stage="部分資料載入失敗", error=str(e))

    # ---- 快取
    def cached(self, key, ttl, fn):
        now = time.time()
        with self._cache_lock:
            hit = self._cache.get(key)
            if hit and now - hit[0] < ttl:
                return hit[1]
            lock = self._key_locks.setdefault(key, threading.Lock())
        with lock:  # 同一檔同時被查只算一次
            with self._cache_lock:
                hit = self._cache.get(key)
                if hit and time.time() - hit[0] < ttl:
                    return hit[1]
            val = fn()
            with self._cache_lock:
                self._cache[key] = (time.time(), val)
                if len(self._cache) > 400:
                    for k, _ in sorted(self._cache.items(), key=lambda kv: kv[1][0])[:100]:
                        self._cache.pop(k, None)
            return val

    def stock(self, code: str, note=None) -> dict:
        code = code.strip().upper()
        return self.cached(("stock", code), STOCK_TTL,
                           lambda: serialize(analyze(code, self.ctx(), note, chart_days=CHART_DAYS)))

    def summaries(self, codes):
        def one(c):
            try:
                return summary_row(self.stock(c)), None
            except Exception as e:  # noqa: BLE001
                return None, {"code": c, "error": str(e)}
        rows, errs = [], []
        for r, e in self.pool.map(one, codes):
            (rows if r else errs).append(r or e)
        return rows, errs

    def scan(self):
        def run():
            c = self.ctx()
            sc = CFG.get("scan") or {}
            picks = c.tw.scan_candidates(int(sc.get("top_n", 6)) + 2, float(sc.get("min_trade_value_million", 100)),
                                         int(sc.get("days", 5)))
            notes = dict(picks)
            rows, errs = self.summaries([p[0] for p in picks])
            for r in rows:
                r["note"] = notes.get(r["code"])
            rows.sort(key=lambda r: -r["score"])
            return {"date": clean(c.tw.recent_inst_days("listed", 1)[0]) if c.tw.recent_inst_days("listed", 1) else None,
                    "rows": rows, "errors": errs, "generated": dt.datetime.now().strftime("%Y-%m-%d %H:%M")}
        return self.cached("scan", SCAN_TTL, run)


engine = Engine()
app = FastAPI(title="股票健診", docs_url="/api/docs", redoc_url=None)


@app.on_event("startup")
def _startup():
    threading.Thread(target=engine.warm, daemon=True).start()


def _fail(e: Exception):
    if isinstance(e, ValueError):
        raise HTTPException(404, str(e))
    raise HTTPException(502, f"資料來源暫時無法取得，請稍後再試（{e}）")


@app.get("/api/health")
def health():
    return {"ok": True, **engine.status, "storage": visits.kind}


@app.get("/api/market")
def market():
    try:
        c = engine.ctx()
        mk, flows = c.market
        return clean({"indices": mk, "flows": flows, "updated": dt.datetime.fromtimestamp(engine._ctx_time)
                      .strftime("%Y-%m-%d %H:%M")})
    except Exception as e:  # noqa: BLE001
        _fail(e)


@app.get("/api/stock/{code}")
def stock(code: str):
    try:
        return engine.stock(code)
    except Exception as e:  # noqa: BLE001
        _fail(e)


@app.get("/api/summary")
def summary(codes: str = Query(..., description="以逗號分隔，最多 30 檔")):
    cs = [c.strip().upper() for c in codes.split(",") if c.strip()][:30]
    rows, errs = engine.summaries(cs)
    return {"rows": rows, "errors": errs}


@app.get("/api/scan")
def scan():
    try:
        return engine.scan()
    except Exception as e:  # noqa: BLE001
        _fail(e)


@app.get("/api/search")
def search(q: str = ""):
    q = q.strip()
    if not q:
        return []
    try:
        names = engine.ctx().tw.names()
    except Exception:  # noqa: BLE001
        names = {}
    qu = q.upper()
    hits = []
    for code, (name, board) in names.items():
        if not (code.isdigit() or code[:-1].isdigit()):
            continue  # 排除權證以外的雜項代號
        if code.startswith(qu) or q in name:
            exact = code == qu or name == q
            hits.append((0 if exact else 1 if code.startswith(qu) else 2, len(code), code,
                         {"code": code, "name": name, "board": "上市" if board == "listed" else "上櫃"}))
    hits.sort(key=lambda x: x[:3])
    out = [h[3] for h in hits[:10]]
    if q.isascii() and q.replace(".", "").replace("-", "").isalpha() and not is_tw(qu):
        out.insert(0, {"code": qu, "name": f"美股 {qu}", "board": "美股"})
    return out


@app.get("/api/model")
def model():
    try:
        with open(os.path.join(ROOT, "model_card.json"), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return JSONResponse({"error": "尚無模型驗證資料"}, status_code=404)


# ------------------------------------------------------------------ 造訪紀錄 / 管理員
ADMIN_USER = os.environ.get("ADMIN_USER", "admin")
ADMIN_PASS = os.environ.get("ADMIN_PASS", "admin")
SECRET = (os.environ.get("SECRET_KEY") or secrets.token_hex(32)).encode()
SESSION_DAYS = 7
COOKIE = "sc_admin"
visits = Store(os.path.join(CACHE_DIR, "visits.sqlite3"))
_login_fail: dict = {}


def _client_ip(req: Request) -> str:
    fwd = req.headers.get("x-forwarded-for", "")
    return fwd.split(",")[0].strip() if fwd else (req.client.host if req.client else "")


def _sign(exp: int) -> str:
    return f"{exp}.{hmac.new(SECRET, str(exp).encode(), hashlib.sha256).hexdigest()}"


def _is_admin(req: Request) -> bool:
    tok = req.cookies.get(COOKIE, "")
    try:
        exp = int(tok.split(".")[0])
    except ValueError:
        return False
    return exp > time.time() and hmac.compare_digest(tok, _sign(exp))


@app.post("/api/visit")
async def visit(req: Request):
    try:
        info = await req.json()
    except Exception:  # noqa: BLE001
        info = {}
    if not isinstance(info, dict):
        info = {}
    ua = req.headers.get("user-agent", "")
    dev = parse_ua(ua, info)
    vid = str(info.get("vid") or "")[:40] or uuid.uuid4().hex[:12]
    visits.add_async({
        "ts": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), "visitor": vid,
        "ip": mask_ip(_client_ip(req)), "ua": ua, **dev, "path": str(info.get("path") or "")[:120],
        "screen": f"{info.get('sw', '')}x{info.get('sh', '')}@{info.get('dpr', '')}",
        "lang": str(info.get("lang") or "")[:20], "referrer": str(info.get("ref") or "")[:200],
    })
    return {"ok": True}


@app.post("/api/login")
async def login(req: Request):
    ip = _client_ip(req)
    fails = [t for t in _login_fail.get(ip, []) if time.time() - t < 600]
    if len(fails) >= 10:
        raise HTTPException(429, "嘗試次數太多，請 10 分鐘後再試")
    try:
        body = await req.json()
    except Exception:  # noqa: BLE001
        body = {}
    u, p = str(body.get("username", "")), str(body.get("password", ""))
    if not (hmac.compare_digest(u, ADMIN_USER) and hmac.compare_digest(p, ADMIN_PASS)):
        _login_fail[ip] = fails + [time.time()]
        raise HTTPException(401, "帳號或密碼錯誤")
    _login_fail.pop(ip, None)
    resp = JSONResponse({"ok": True})
    https = req.headers.get("x-forwarded-proto", req.url.scheme) == "https"
    resp.set_cookie(COOKIE, _sign(int(time.time()) + SESSION_DAYS * 86400), max_age=SESSION_DAYS * 86400,
                    httponly=True, samesite="lax", secure=https)
    return resp


@app.post("/api/logout")
def logout():
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(COOKIE)
    return resp


@app.get("/api/admin/me")
def admin_me(req: Request):
    return {"admin": _is_admin(req)}


@app.get("/api/admin/visits")
def admin_visits(req: Request, limit: int = 300, bots: int = 0):
    if not _is_admin(req):
        raise HTTPException(401, "請先登入")
    try:
        rows = visits.recent(20000)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"讀取造訪紀錄失敗：{e}")
    stats = summarize(rows, bool(bots))
    shown = [r for r in rows if bots or r["device"] != "機器人"][: max(1, min(limit, 2000))]
    out = []
    for r in shown:
        r = dict(r)
        r["time"] = to_local(r["ts"]).strftime("%Y-%m-%d %H:%M:%S")
        r.pop("_local", None)
        out.append(r)
    return {"stats": stats, "rows": out, "storage": visits.kind, "storage_error": visits.error,
            "default_password": ADMIN_PASS == "admin"}


@app.get("/api/defaults")
def defaults():
    return {"watchlist": CFG.get("watchlist", []), "capital": CFG.get("capital", 1_000_000),
            "risk_per_trade_pct": CFG.get("risk_per_trade_pct", 1.0), "max_position_pct": CFG.get("max_position_pct", 20)}


app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.get("/")
def index():
    return FileResponse(os.path.join(STATIC, "index.html"), headers={"Cache-Control": "no-cache"})
