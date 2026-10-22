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

# 證交所/櫃買中心對短時間大量請求會封 IP，非快取請求之間至少間隔這麼久
_THROTTLE_HOSTS = ("www.twse.com.tw", "www.tpex.org.tw")
_THROTTLE_SEC = 2.2
_last_hit: dict = {}
_throttle_lock = threading.Lock()


def _cache_path(url: str) -> str:
    return os.path.join(CACHE_DIR, hashlib.sha1(url.encode("utf-8")).hexdigest() + ".json")


def _throttle(url: str) -> None:
    host = url.split("/")[2]
    if host not in _THROTTLE_HOSTS:
        return
    # 鎖住期間 sleep，讓多執行緒（網站）下對同一主機的請求也是排隊送出
    with _throttle_lock:
        wait = _THROTTLE_SEC - (time.time() - _last_hit.get(host, 0))
        if wait > 0:
            time.sleep(wait)
        _last_hit[host] = time.time()


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
