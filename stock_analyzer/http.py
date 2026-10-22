"""HTTP 抓取 + 磁碟快取 + 重試 + TWSE/TPEx 節流（只用標準函式庫）。"""
from __future__ import annotations

import hashlib
import json
import os
import threading
import time
import urllib.error
import urllib.request

CACHE_DIR = os.environ.get("STOCK_CACHE_DIR") or os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "cache")
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"

# 官方網站對短時間大量請求會封 IP：非快取請求之間至少間隔這麼久（每個主機各自排隊，不互相卡住）
_THROTTLE = {"www.twse.com.tw": 2.2, "www.tpex.org.tw": 2.2, "mopsov.twse.com.tw": 1.5,
             "www.tdcc.com.tw": 1.5, "news.google.com": 1.0, "tw.stock.yahoo.com": 0.5}
_last_hit: dict = {}
_host_locks: dict = {}
_locks_guard = threading.Lock()


def _cache_path(url: str, ext: str = ".json") -> str:
    return os.path.join(CACHE_DIR, hashlib.sha1(url.encode("utf-8")).hexdigest() + ext)


def _throttle(url: str) -> None:
    host = url.split("/")[2]
    gap = _THROTTLE.get(host)
    if not gap:
        return
    with _locks_guard:
        lock = _host_locks.setdefault(host, threading.Lock())
    # 鎖住期間 sleep，讓多執行緒下對同一主機的請求也是排隊送出
    with lock:
        wait = gap - (time.time() - _last_hit.get(host, 0))
        if wait > 0:
            time.sleep(wait)
        _last_hit[host] = time.time()


def get_text(url: str, ttl: float | None = 1800, encoding: str = "utf-8", data: bytes | None = None,
             headers: dict | None = None, keep=None, retries: int = 3, opener=None) -> str:
    """抓文字（HTML / RSS / CSV），快取規則同 get_json。data 不為 None 時以 POST 送出（快取鍵含 data）。"""
    os.makedirs(CACHE_DIR, exist_ok=True)
    key = url + ("|" + data.decode("utf-8", "replace") if data else "")
    path = _cache_path(key, ".txt")
    if os.path.exists(path) and (ttl is None or time.time() - os.path.getmtime(path) < ttl):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return f.read()
        except OSError:
            pass
    last_err = None
    for attempt in range(retries):
        try:
            _throttle(url)
            h = {"User-Agent": UA}
            h.update(headers or {})
            req = urllib.request.Request(url, headers=h, data=data)
            with (opener.open(req, timeout=30) if opener else urllib.request.urlopen(req, timeout=30)) as resp:
                raw = resp.read()
            if raw[:2] == bytes([0x1F, 0x8B]):  # gzip
                import gzip
                raw = gzip.decompress(raw)
            text = raw.decode(encoding, "replace")
            if keep is None or keep(text):
                tmp = f"{path}.{threading.get_ident()}.tmp"
                with open(tmp, "w", encoding="utf-8") as f:
                    f.write(text)
                os.replace(tmp, path)
            return text
        except urllib.error.HTTPError as e:
            last_err = e
            if e.code == 404:
                break
        except (urllib.error.URLError, OSError, ValueError) as e:
            last_err = e
        time.sleep(2 * (attempt + 1))
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    raise RuntimeError(f"抓取失敗 {url}: {last_err}")


def get_json(url: str, ttl: float | None = 1800, retries: int = 3, keep=None):
    """抓 JSON。ttl=None 表示永久快取（歷史日期資料不會再變）。

    keep(data) -> bool：回傳 False 的結果不寫入快取（例如當天資料尚未公布）。
    """
    os.makedirs(CACHE_DIR, exist_ok=True)
    path = _cache_path(url)
    if os.path.exists(path):
        age = time.time() - os.path.getmtime(path)
        if ttl is None or age < ttl:
            try:
                with open(path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except (OSError, ValueError):
                pass

    last_err = None
    for attempt in range(retries):
        try:
            _throttle(url)
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=30) as resp:
                raw = resp.read()
            data = json.loads(raw.decode("utf-8-sig"))
            if keep is None or keep(data):
                tmp = f"{path}.{threading.get_ident()}.tmp"
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False)
                os.replace(tmp, path)
            return data
        except urllib.error.HTTPError as e:
            last_err = e
            if e.code == 404:
                break
            if e.code == 429 and "query1.finance.yahoo.com" in url:
                url = url.replace("query1.", "query2.")  # Yahoo 限流時改走備援主機
        except (urllib.error.URLError, OSError, ValueError) as e:
            last_err = e
        time.sleep(2 * (attempt + 1))

    # 網路失敗時退而求其次用過期快取
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            pass
    raise RuntimeError(f"抓取失敗 {url}: {last_err}")


def purge_cache(max_age_days: float = 30) -> None:
    """清掉過舊的快取檔，避免 cache 資料夾無限長大。"""
    if not os.path.isdir(CACHE_DIR):
        return
    cutoff = time.time() - max_age_days * 86400
    for name in os.listdir(CACHE_DIR):
        p = os.path.join(CACHE_DIR, name)
        try:
            if os.path.getmtime(p) < cutoff:
                os.remove(p)
        except OSError:
            pass
